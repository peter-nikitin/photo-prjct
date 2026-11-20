package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestRuleLifecycle(t *testing.T) {
	var requests []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests = append(requests, r.Method+" "+r.URL.RequestURI())
		if r.Header.Get("Authorization") != "Bearer test-token" {
			t.Error("missing credentials")
		}
		switch r.Method + " " + r.URL.Path {
		case "POST /cdn/v1/rules":
			var payload apiRule
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				t.Error("invalid JSON request")
			}
			if payload.ResourceID != "resource1" ||
				!strings.Contains(string(payload.Options["secureKey"]), "secret-key") {
				t.Error("invalid create payload")
			}
			fmt.Fprint(w, `{"id":"create-operation","done":false}`)
		case "GET /operations/create-operation":
			fmt.Fprint(w, `{"id":"create-operation","done":true,"response":{"id":"42"}}`)
		case "GET /cdn/v1/rules/42":
			fmt.Fprint(w, `{"id":"42","name":"archives","rulePattern":"^/archive$","weight":"40","originsGroupId":"12","originProtocol":"HTTPS","options":{"secureKey":{"enabled":true,"type":"DISABLE_IP_SIGNING","key":"secret-key"},"staticRequestHeaders":{"enabled":true,"value":{"X-FindMe-Origin-Auth":"secret-header"}}}}`)
		case "PATCH /cdn/v1/rules/42":
			var payload apiRule
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				t.Error("invalid JSON request")
			}
			if payload.ResourceID != "resource1" || payload.OriginGroupID != "0" ||
				payload.UpdateMask != "options.secureKey,options.staticRequestHeaders" {
				t.Error("update must reset the origin group and removed options")
			}
			fmt.Fprint(w, `{"done":true,"response":{"id":"42"}}`)
		case "DELETE /cdn/v1/rules/42":
			fmt.Fprint(w, `{"done":true,"response":{}}`)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	client := cdnClient{
		apiURL: server.URL + "/cdn/v1", operationURL: server.URL + "/operations",
		token: "test-token", http: server.Client(), pollInterval: time.Nanosecond,
	}
	ctx := t.Context()
	rule := apiRule{
		ResourceID: "resource1", Name: "archives", Pattern: "^/archive$", Weight: "40",
		Options: map[string]json.RawMessage{
			"secureKey": json.RawMessage(`{"enabled":true,"key":"secret-key"}`),
		},
	}
	id, err := client.create(ctx, rule)
	if err != nil || id != "42" {
		t.Fatalf("create: id=%q error=%v", id, err)
	}
	resourceID, importedID, err := parseImportID("resource1/42")
	if err != nil || resourceID != "resource1" || importedID != id {
		t.Fatalf("invalid identity: %q/%q, %v", resourceID, importedID, err)
	}
	read, found, err := client.read(ctx, resourceID, importedID)
	if err != nil || !found || read.Name != "archives" {
		t.Fatalf("read: found=%t error=%v", found, err)
	}
	state, err := stateOptions(read.Options)
	if err != nil || state != `{"secureKey":{"enabled":true,"type":"DISABLE_IP_SIGNING"},"staticRequestHeaders":{"enabled":true,"value":{"X-FindMe-Origin-Auth":""}}}` {
		t.Fatal("read did not redact the rule options")
	}
	if err := client.update(ctx, id, rule, []string{"secureKey", "staticRequestHeaders"}); err != nil {
		t.Fatalf("update: %v", err)
	}
	if err := client.delete(ctx, resourceID, id); err != nil {
		t.Fatalf("delete: %v", err)
	}
	if _, found, err := client.read(ctx, resourceID, "99"); found || err != nil {
		t.Fatalf("missing: found=%t error=%v", found, err)
	}
	want := []string{
		"POST /cdn/v1/rules", "GET /operations/create-operation",
		"GET /cdn/v1/rules/42?resourceId=resource1", "PATCH /cdn/v1/rules/42",
		"DELETE /cdn/v1/rules/42?resourceId=resource1", "GET /cdn/v1/rules/99?resourceId=resource1",
	}
	if !reflect.DeepEqual(requests, want) {
		t.Errorf("requests: %v; expected %v", requests, want)
	}
}

func TestOptionsRequireWriteOnlySecrets(t *testing.T) {
	const desired = `{"secureKey":{"enabled":true,"type":"DISABLE_IP_SIGNING"},"staticRequestHeaders":{"enabled":true,"value":{"X-FindMe-Origin-Auth":""}}}`
	options, err := desiredOptions(desired)
	if err != nil {
		t.Fatal(err)
	}
	if err := addSecrets(options, "secret-key", "secret-header"); err != nil {
		t.Fatal(err)
	}
	payload, _ := json.Marshal(options)
	if !strings.Contains(string(payload), "secret-key") || !strings.Contains(string(payload), "secret-header") {
		t.Fatal("write-only credentials must be sent to Yandex")
	}
	for _, unsafe := range []string{
		`{"secureKey":{"enabled":true,"key":"secret-value"}}`,
		`{"staticRequestHeaders":{"enabled":true,"value":{"X-FindMe-Origin-Auth":"secret-value"}}}`,
		`{"staticRequestHeaders":{"enabled":true,"value":{"Authorization":""}}}`,
	} {
		if _, err := desiredOptions(unsafe); err == nil || strings.Contains(err.Error(), "secret-value") {
			t.Fatal("invalid options must produce a diagnostic without credentials")
		}
	}
	options, _ = desiredOptions(desired)
	if addSecrets(options, "", "secret-header") == nil {
		t.Fatal("missing signing key was accepted")
	}
}

func TestCloudErrorsDoNotContainResponseBody(t *testing.T) {
	for _, failed := range []struct {
		status int
		body   string
	}{
		{403, `{"message":"secret-value"}`},
		{200, `{"done":true,"error":{"code":3,"message":"secret-value"}}`},
	} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(failed.status)
			fmt.Fprint(w, failed.body)
		}))
		client := cdnClient{apiURL: server.URL, http: server.Client(), token: "test-token"}
		_, err := client.create(context.Background(), apiRule{})
		server.Close()
		if err == nil || strings.Contains(err.Error(), "secret-value") {
			t.Fatal("API failures must produce errors without response bodies")
		}
	}
}

func TestStateOptionsCanonicalizesNestedObjects(t *testing.T) {
	options := map[string]json.RawMessage{
		"edgeCacheSettings": json.RawMessage(`{"value":{"customValues":{"206":"600","200":"600"}},"enabled":true}`),
	}
	state, err := stateOptions(options)
	want := `{"edgeCacheSettings":{"enabled":true,"value":{"customValues":{"200":"600","206":"600"}}}}`
	if err != nil || state != want {
		t.Fatalf("state=%q, want=%q, error=%v", state, want, err)
	}
}

func TestRuleUsesLiveCredentialsOnlyWhenNeeded(t *testing.T) {
	const resourceID = "resource1"
	var reads []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Query().Get("resourceId") != resourceID {
			t.Error("saving the rule payload must only read from its existing CDN endpoint")
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		reads = append(reads, r.URL.Path)
		switch r.URL.Path {
		case "/rules/42":
			fmt.Fprint(w, `{"id":"42","name":"originals","rulePattern":"^/original$","options":{"secureKey":{"enabled":true,"key":"live-originals-key"},"staticRequestHeaders":{"enabled":true,"value":{"X-FindMe-Origin-Auth":"live-origin-header"}}}}`)
		case "/rules/43":
			fmt.Fprint(w, `{"id":"43","name":"previews","rulePattern":"^/previews$","options":{"secureKey":{"enabled":true,"key":"live-previews-key"}}}`)
		case "/rules/44":
			fmt.Fprint(w, `{"id":"44","name":"disabled","rulePattern":"^/disabled$","options":{"secureKey":{"enabled":false,"key":"disabled-key"}}}`)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	client := cdnClient{apiURL: server.URL, token: "test-token", http: server.Client()}
	signing := `"secureKey":{"enabled":true,"type":"DISABLE_IP_SIGNING"}`
	header := `"staticRequestHeaders":{"enabled":true,"value":{"X-FindMe-Origin-Auth":""}}`
	for _, tc := range []struct {
		name, options, existingID, sourceID, key, auth, wantKey, wantHeader string
		wantReads                                                           []string
		wantError                                                           bool
	}{
		{name: "archive creation", options: "{" + signing + "}", sourceID: "43", wantKey: "live-previews-key", wantReads: []string{"/rules/43"}},
		{name: "originals update", options: "{" + signing + "," + header + "}", existingID: "42", wantKey: "live-originals-key", wantHeader: "live-origin-header", wantReads: []string{"/rules/42"}},
		{name: "explicit override", options: "{" + signing + "," + header + "}", existingID: "42", sourceID: "43", key: "new-key", auth: "new-header", wantKey: "new-key", wantHeader: "new-header"},
		{name: "unsigned update", options: "{}", existingID: "42"},
		{name: "no source for creation", options: "{" + signing + "}", wantError: true},
		{name: "invalid source", options: "{" + signing + "}", sourceID: "../43", wantError: true},
		{name: "disabled signing", options: "{" + signing + "}", sourceID: "44", wantReads: []string{"/rules/44"}, wantError: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			reads = nil
			options, err := desiredOptions(tc.options)
			if err != nil {
				t.Fatal(err)
			}
			err = client.setRuleCredentials(t.Context(), apiRule{ResourceID: resourceID, Options: options}, tc.existingID, tc.sourceID, tc.key, tc.auth)
			if (err != nil) != tc.wantError || (err != nil && strings.Contains(err.Error(), "disabled-key")) {
				t.Fatalf("unexpected credentials error: %v", err)
			}
			if !reflect.DeepEqual(reads, tc.wantReads) {
				t.Fatalf("read %v, expected %v", reads, tc.wantReads)
			}
			data, _ := json.Marshal(options)
			for _, expected := range []string{tc.wantKey, tc.wantHeader} {
				if expected != "" && !strings.Contains(string(data), expected) {
					t.Error("request payload did not include the expected credential")
				}
			}
			state, err := stateOptions(options)
			if err != nil {
				t.Fatal(err)
			}
			for _, secret := range []string{"live-previews-key", "live-originals-key", "live-origin-header", "new-key", "new-header", "disabled-key"} {
				if strings.Contains(state, secret) {
					t.Error("state exposed a credential")
				}
			}
		})
	}
}
