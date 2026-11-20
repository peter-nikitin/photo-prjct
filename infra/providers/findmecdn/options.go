package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
)

var errOptions = errors.New("CDN options must be a JSON object with supported options and no embedded credentials")

func object(data json.RawMessage) (map[string]json.RawMessage, error) {
	var result map[string]json.RawMessage
	if err := json.Unmarshal(data, &result); err != nil || result == nil {
		return nil, errOptions
	}
	return result, nil
}

func validateOptions(options map[string]json.RawMessage, reading bool) error {
	for name, raw := range options {
		switch name {
		case "disableCache", "edgeCacheSettings", "browserCacheSettings", "cacheHttpHeaders",
			"queryParamsOptions", "slice", "hostOptions", "customServerName", "staticHeaders", "staticResponse",
			"allowedHttpMethods", "ignoreCookie":
			if _, err := object(raw); err != nil {
				return err
			}
		case "secureKey":
			key, err := object(raw)
			if err != nil {
				return err
			}
			for field := range key {
				switch field {
				case "enabled", "type":
				case "key":
					if !reading {
						return errOptions
					}
					delete(key, field)
				default:
					return errOptions
				}
			}
			options[name], _ = json.Marshal(key)
		case "staticRequestHeaders":
			headers, err := object(raw)
			if err != nil {
				return err
			}
			for field, value := range headers {
				switch field {
				case "enabled":
				case "value":
					var values map[string]string
					if err := json.Unmarshal(value, &values); err != nil || values == nil {
						return errOptions
					}
					for header, text := range values {
						if header != originAuthHeader || (!reading && text != "") {
							return errOptions
						}
						values[header] = ""
					}
					headers[field], _ = json.Marshal(values)
				default:
					return errOptions
				}
			}
			options[name], _ = json.Marshal(headers)
		default:
			return errOptions
		}
	}
	return nil
}

func stateOptions(options map[string]json.RawMessage) (string, error) {
	if options == nil {
		options = map[string]json.RawMessage{}
	}
	if err := validateOptions(options, true); err != nil {
		return "", err
	}
	canonical := make(map[string]any, len(options))
	for name, data := range options {
		var value any
		decoder := json.NewDecoder(bytes.NewReader(data))
		decoder.UseNumber()
		if err := decoder.Decode(&value); err != nil {
			return "", errOptions
		}
		canonical[name] = value
	}
	data, err := json.Marshal(canonical)
	if err != nil {
		return "", errOptions
	}
	return string(data), nil
}

func desiredOptions(text string) (map[string]json.RawMessage, error) {
	options, err := object(json.RawMessage(text))
	if err != nil {
		return nil, err
	}
	if err := validateOptions(options, false); err != nil {
		return nil, err
	}
	return options, nil
}

func addSecrets(options map[string]json.RawMessage, signingKey, originAuth string) error {
	if data, ok := options["secureKey"]; ok {
		key, err := object(data)
		if err != nil {
			return err
		}
		var enabled bool
		if data, ok := key["enabled"]; ok {
			if err := json.Unmarshal(data, &enabled); err != nil {
				return errOptions
			}
		}
		if enabled {
			if signingKey == "" {
				return errors.New("secureKey is enabled but there is no signing key in the selected CDN rule or secure_key_wo")
			}
			key["key"], _ = json.Marshal(signingKey)
			options["secureKey"], _ = json.Marshal(key)
		}
	}
	if data, ok := options["staticRequestHeaders"]; ok {
		headers, err := object(data)
		if err != nil {
			return err
		}
		var enabled bool
		if data, ok := headers["enabled"]; ok {
			if err := json.Unmarshal(data, &enabled); err != nil {
				return errOptions
			}
		}
		if enabled {
			var values map[string]string
			if err := json.Unmarshal(headers["value"], &values); err != nil || len(values) != 1 {
				return fmt.Errorf("enabled staticRequestHeaders must declare an empty %s placeholder", originAuthHeader)
			}
			if originAuth == "" {
				return errors.New("staticRequestHeaders is enabled but there is no origin credential in the current CDN rule or origin_auth_wo")
			}
			values[originAuthHeader] = originAuth
			headers["value"], _ = json.Marshal(values)
			options["staticRequestHeaders"], _ = json.Marshal(headers)
		}
	}
	return nil
}

type liveSigningOption struct {
	Enabled bool   `json:"enabled"`
	Key     string `json:"key"`
}

type liveHeaderOption struct {
	Enabled bool              `json:"enabled"`
	Value   map[string]string `json:"value"`
}

func (c *cdnClient) setRuleCredentials(ctx context.Context, rule apiRule, existingID, sourceID, signingKey, originAuth string) error {
	if sourceID != "" && !validRuleID(sourceID) {
		return errors.New("secure_key_source_rule_id must be a positive numeric CDN rule ID")
	}

	var signing liveSigningOption
	var auth liveHeaderOption
	if data, ok := rule.Options["secureKey"]; ok {
		if err := json.Unmarshal(data, &signing); err != nil {
			return errOptions
		}
	}
	if data, ok := rule.Options["staticRequestHeaders"]; ok {
		if err := json.Unmarshal(data, &auth); err != nil {
			return errOptions
		}
	}
	needKey := signing.Enabled && signingKey == ""
	needAuth := auth.Enabled && originAuth == ""
	if sourceID == "" {
		sourceID = existingID
	}

	// Read the current rule only if a required credential comes from it.
	var existing apiRule
	if existingID != "" && (needAuth || (needKey && sourceID == existingID)) {
		var err error
		existing, err = c.readCredentialRule(ctx, rule.ResourceID, existingID)
		if err != nil {
			return err
		}
	}
	if needKey && sourceID != "" {
		source := existing
		if sourceID != existingID {
			var err error
			source, err = c.readCredentialRule(ctx, rule.ResourceID, sourceID)
			if err != nil {
				return err
			}
		}
		if data, ok := source.Options["secureKey"]; ok {
			var live liveSigningOption
			if err := json.Unmarshal(data, &live); err != nil {
				return errOptions
			}
			if live.Enabled {
				signingKey = live.Key
			}
		}
	}
	if needAuth {
		if data, ok := existing.Options["staticRequestHeaders"]; ok {
			var live liveHeaderOption
			if err := json.Unmarshal(data, &live); err != nil {
				return errOptions
			}
			if live.Enabled {
				originAuth = live.Value[originAuthHeader]
			}
		}
	}
	return addSecrets(rule.Options, signingKey, originAuth)
}

func (c *cdnClient) readCredentialRule(ctx context.Context, resourceID, id string) (apiRule, error) {
	rule, found, err := c.read(ctx, resourceID, id)
	if err != nil {
		return apiRule{}, err
	}
	if !found {
		return apiRule{}, errors.New("CDN rule providing credentials was not found")
	}
	return rule, nil
}
