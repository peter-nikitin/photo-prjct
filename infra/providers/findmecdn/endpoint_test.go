package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

func TestReadEndpointRetainsLiveOriginAndTLS(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/resources/cdn-id" {
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		_, _ = w.Write([]byte(`{
		  "id":"cdn-id","folderId":"folder-id","cname":"img.findme-photo.ru",
		  "active":true,"providerType":"ourcdn","originGroupId":"1234","originProtocol":"HTTPS",
		  "sslCertificate":{"type":"CM","status":"READY","data":{"cm":{"id":"cert-id"}}},
		  "tls":{"profile":"PROFILE_COMPATIBLE"},
		  "options":{"allowedHttpMethods":{"value":["GET","HEAD"],"enabled":true},
		             "ignoreCookie":{"value":true,"enabled":true}}
		}`))
	}))
	defer server.Close()

	client := cdnClient{apiURL: server.URL, token: "test", http: server.Client()}
	state, err := client.readEndpoint(context.Background(), "cdn-id")
	if err != nil {
		t.Fatal(err)
	}
	if state.OriginGroupID.ValueString() != "1234" ||
		state.CertificateManagerID.ValueString() != "cert-id" ||
		state.TLSProfile.ValueString() != "PROFILE_COMPATIBLE" ||
		!state.Active.ValueBool() {
		t.Fatalf("read dropped the live origin, certificate or TLS settings: %+v", state)
	}
	request, err := state.request()
	if err != nil {
		t.Fatal(err)
	}
	if request.Certificate.Type != "CM" || request.Certificate.Data.CM.ID != "cert-id" ||
		request.OriginProtocol != "HTTPS" || request.TLS.Profile != "PROFILE_COMPATIBLE" {
		t.Fatalf("request did not preserve endpoint settings: %+v", request)
	}
	if string(request.Options["allowedHttpMethods"]) != `{"enabled":true,"value":["GET","HEAD"]}` {
		t.Fatalf("unexpected options: %s", request.Options["allowedHttpMethods"])
	}
}

func TestEndpointRequestRejectsCredentialsInOptions(t *testing.T) {
	for _, options := range []string{
		`{"secureKey":{"enabled":true,"type":"DISABLE_IP_SIGNING"}}`,
		`{"secureKey":{"enabled":false,"key":"secret"}}`,
		`{"staticRequestHeaders":{"enabled":true,"value":{"X-FindMe-Origin-Auth":"secret"}}}`,
	} {
		var response validator.StringResponse
		endpointOptionsValidator{}.ValidateString(context.Background(), validator.StringRequest{
			Path:        path.Root("options_json"),
			ConfigValue: types.StringValue(options),
		}, &response)
		if !response.Diagnostics.HasError() {
			t.Fatal("accepted credentials in endpoint options")
		}
	}
}
