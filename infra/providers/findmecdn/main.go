package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/provider"
	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/hashicorp/terraform-plugin-framework/resource"
)

const providerVersion = "0.1.0"

type cdnProvider struct{}

func (cdnProvider) Metadata(_ context.Context, _ provider.MetadataRequest, resp *provider.MetadataResponse) {
	resp.TypeName = "findmecdn"
	resp.Version = providerVersion
}

func (cdnProvider) Schema(context.Context, provider.SchemaRequest, *provider.SchemaResponse) {}

func (cdnProvider) Configure(_ context.Context, _ provider.ConfigureRequest, resp *provider.ConfigureResponse) {
	resp.ResourceData = &cdnClient{
		apiURL:       "https://cdn.api.cloud.yandex.net/cdn/v1",
		operationURL: "https://operation.api.cloud.yandex.net/operations",
		token:        os.Getenv("YC_TOKEN"),
		http: &http.Client{
			Timeout: 30 * time.Second,
			CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
				return http.ErrUseLastResponse
			},
		},
		pollInterval: time.Second,
	}
}

func (cdnProvider) Resources(context.Context) []func() resource.Resource {
	return []func() resource.Resource{
		NewEndpoint,
		func() resource.Resource { return &ruleResource{} },
	}
}

func (cdnProvider) DataSources(context.Context) []func() datasource.DataSource {
	return nil
}

func main() {
	err := providerserver.Serve(context.Background(), func() provider.Provider { return cdnProvider{} }, providerserver.ServeOpts{
		Address: "terraform.findme-photo.ru/findme/findmecdn",
	})
	if err != nil {
		log.Fatal(err)
	}
}
