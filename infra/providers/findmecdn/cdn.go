package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

const originAuthHeader = "X-FindMe-Origin-Auth"

var resourceIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,49}$`)

type apiRule struct {
	ID             string                     `json:"id,omitempty"`
	ResourceID     string                     `json:"resourceId,omitempty"`
	Name           string                     `json:"name"`
	Pattern        string                     `json:"rulePattern"`
	Weight         string                     `json:"weight"`
	OriginGroupID  string                     `json:"originsGroupId,omitempty"`
	OriginProtocol string                     `json:"originProtocol,omitempty"`
	Options        map[string]json.RawMessage `json:"options"`
	UpdateMask     string                     `json:"updateMask,omitempty"`
}

type operation struct {
	ID       string          `json:"id"`
	Done     bool            `json:"done"`
	Response json.RawMessage `json:"response"`
	Error    *struct {
		Code int `json:"code"`
	} `json:"error"`
}

type cdnClient struct {
	apiURL       string
	operationURL string
	token        string
	http         *http.Client
	pollInterval time.Duration
}

type httpStatusError int

func (e httpStatusError) Error() string {
	return fmt.Sprintf("Yandex Cloud CDN returned HTTP %d", e)
}

func (c *cdnClient) request(ctx context.Context, method, address string, payload any, result any) error {
	if c.token == "" {
		return errors.New("YC_TOKEN is required to access Yandex Cloud CDN")
	}
	var body io.Reader
	if payload != nil {
		data, err := json.Marshal(payload)
		if err != nil {
			return errors.New("could not encode CDN request")
		}
		body = bytes.NewReader(data)
	}
	request, err := http.NewRequestWithContext(ctx, method, address, body)
	if err != nil {
		return errors.New("could not build CDN request")
	}
	request.Header.Set("Authorization", "Bearer "+c.token)
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := c.http.Do(request)
	if err != nil {
		return errors.New("CDN request did not complete")
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return httpStatusError(response.StatusCode)
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(result); err != nil {
		return errors.New("could not decode CDN response")
	}
	return nil
}

func validRuleID(value string) bool {
	id, err := strconv.ParseInt(value, 10, 64)
	return err == nil && id > 0 && strconv.FormatInt(id, 10) == value
}

func parseImportID(value string) (string, string, error) {
	resourceID, ruleID, ok := strings.Cut(value, "/")
	if !ok || !resourceIDPattern.MatchString(resourceID) || !validRuleID(ruleID) {
		return "", "", errors.New("expected <CDN resource ID>/<numeric rule ID>")
	}
	return resourceID, ruleID, nil
}

func (c *cdnClient) ruleURL(id, resourceID string) string {
	address := c.apiURL + "/rules/" + url.PathEscape(id)
	if resourceID != "" {
		address += "?resourceId=" + url.QueryEscape(resourceID)
	}
	return address
}

func (c *cdnClient) read(ctx context.Context, resourceID, id string) (apiRule, bool, error) {
	var rule apiRule
	err := c.request(ctx, http.MethodGet, c.ruleURL(id, resourceID), nil, &rule)
	if errors.Is(err, httpStatusError(http.StatusNotFound)) {
		return apiRule{}, false, nil
	}
	if err != nil {
		return apiRule{}, false, err
	}
	if rule.ID != id || rule.Name == "" || rule.Pattern == "" {
		return apiRule{}, false, errors.New("CDN returned an incomplete rule or a different ID")
	}
	return rule, true, nil
}

func (c *cdnClient) modify(ctx context.Context, method, address string, payload any) (json.RawMessage, error) {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	var op operation
	if err := c.request(ctx, method, address, payload, &op); err != nil {
		return nil, err
	}
	for {
		if op.Error != nil {
			if resourceIDPattern.MatchString(op.ID) {
				return nil, fmt.Errorf("CDN operation %s failed with code %d", op.ID, op.Error.Code)
			}
			return nil, fmt.Errorf("CDN operation failed with code %d", op.Error.Code)
		}
		if op.Done {
			if len(op.Response) == 0 || bytes.Equal(op.Response, []byte("null")) {
				return nil, errors.New("CDN operation did not return a response")
			}
			return op.Response, nil
		}
		if op.ID == "" {
			return nil, errors.New("CDN operation did not return an ID")
		}
		timer := time.NewTimer(c.pollInterval)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil, errors.New("timed out waiting for CDN operation")
		case <-timer.C:
		}
		address := c.operationURL + "/" + url.PathEscape(op.ID)
		op = operation{}
		if err := c.request(ctx, http.MethodGet, address, nil, &op); err != nil {
			return nil, err
		}
	}
}

func (c *cdnClient) create(ctx context.Context, rule apiRule) (string, error) {
	data, err := c.modify(ctx, http.MethodPost, c.apiURL+"/rules", rule)
	if err != nil {
		return "", err
	}
	var identity struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(data, &identity); err != nil || !validRuleID(identity.ID) {
		return "", errors.New("CDN create operation did not return the numeric rule ID")
	}
	return identity.ID, nil
}

func (c *cdnClient) update(ctx context.Context, id string, rule apiRule, previousOptions []string) error {
	// The API uses zero to reset the override and treats an absent group as no change.
	if rule.OriginGroupID == "" {
		rule.OriginGroupID = "0"
	}
	// Include removed options so the API resets them to inheritance.
	fields := make([]string, 0, len(rule.Options)+len(previousOptions))
	for name := range rule.Options {
		fields = append(fields, "options."+name)
	}
	for _, name := range previousOptions {
		if _, retained := rule.Options[name]; !retained {
			fields = append(fields, "options."+name)
		}
	}
	slices.Sort(fields)
	rule.UpdateMask = strings.Join(fields, ",")
	_, err := c.modify(ctx, http.MethodPatch, c.ruleURL(id, ""), rule)
	return err
}

func (c *cdnClient) delete(ctx context.Context, resourceID, id string) error {
	_, err := c.modify(ctx, http.MethodDelete, c.ruleURL(id, resourceID), nil)
	if errors.Is(err, httpStatusError(http.StatusNotFound)) {
		return nil
	}
	return err
}
