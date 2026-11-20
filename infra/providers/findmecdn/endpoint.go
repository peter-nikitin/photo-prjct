package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"

	"github.com/hashicorp/terraform-plugin-framework-jsontypes/jsontypes"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"

	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

type endpointResource struct {
	client *cdnClient
}

type endpointModel struct {
	ID                   types.String         `tfsdk:"id"`
	FolderID             types.String         `tfsdk:"folder_id"`
	CNAME                types.String         `tfsdk:"cname"`
	Active               types.Bool           `tfsdk:"active"`
	ProviderType         types.String         `tfsdk:"provider_type"`
	OriginGroupID        types.String         `tfsdk:"origin_group_id"`
	OriginProtocol       types.String         `tfsdk:"origin_protocol"`
	CertificateManagerID types.String         `tfsdk:"certificate_manager_id"`
	TLSProfile           types.String         `tfsdk:"tls_profile"`
	Options              jsontypes.Normalized `tfsdk:"options_json"`
}

type endpointOrigin struct {
	GroupID string `json:"originGroupId"`
}

type endpointCertificate struct {
	Type string `json:"type"`
	Data struct {
		CM struct {
			ID string `json:"id"`
		} `json:"cm"`
	} `json:"data"`
}

type endpointTLS struct {
	Profile string `json:"profile"`
}

type apiEndpoint struct {
	ID             string                     `json:"id,omitempty"`
	FolderID       string                     `json:"folderId,omitempty"`
	CNAME          string                     `json:"cname,omitempty"`
	Active         bool                       `json:"active"`
	ProviderType   string                     `json:"providerType,omitempty"`
	Origin         *endpointOrigin            `json:"origin,omitempty"`
	OriginGroupID  string                     `json:"originGroupId,omitempty"`
	OriginProtocol string                     `json:"originProtocol"`
	Certificate    endpointCertificate        `json:"sslCertificate"`
	TLS            endpointTLS                `json:"tls"`
	Options        map[string]json.RawMessage `json:"options"`
	UpdateMask     string                     `json:"updateMask,omitempty"`
}

func NewEndpoint() resource.Resource {
	return &endpointResource{}
}

func (*endpointResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_endpoint"
}

func (*endpointResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	immutable := schema.StringAttribute{
		Required:      true,
		PlanModifiers: []planmodifier.String{stringplanmodifier.RequiresReplace()},
	}
	required := schema.StringAttribute{Required: true}
	resp.Schema = schema.Schema{
		Description: "Yandex Cloud CDN endpoint with an existing origin group and Certificate Manager certificate.",
		Attributes: map[string]schema.Attribute{
			"id":                     schema.StringAttribute{Computed: true, PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()}},
			"folder_id":              immutable,
			"cname":                  immutable,
			"provider_type":          immutable,
			"origin_group_id":        required,
			"origin_protocol":        required,
			"certificate_manager_id": required,
			"tls_profile":            required,
			"active":                 schema.BoolAttribute{Required: true},
			"options_json": schema.StringAttribute{
				Required:   true,
				CustomType: jsontypes.NormalizedType{},
				Validators: []validator.String{endpointOptionsValidator{}},
			},
		},
	}
}

func (r *endpointResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	if req.ProviderData == nil {
		return
	}
	client, ok := req.ProviderData.(*cdnClient)
	if !ok {
		resp.Diagnostics.AddError("Configure CDN endpoint", "Missing CDN API client.")
		return
	}
	r.client = client
}

func (*endpointResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	if !resourceIDPattern.MatchString(req.ID) {
		resp.Diagnostics.AddError("Import CDN endpoint", "Expected a CDN resource ID.")
		return
	}
	resource.ImportStatePassthroughID(ctx, path.Root("id"), req, resp)
}

func (e endpointModel) request() (apiEndpoint, error) {
	options, err := endpointOptions(e.Options.ValueString())
	if err != nil {
		return apiEndpoint{}, err
	}
	request := apiEndpoint{
		Active:         e.Active.ValueBool(),
		OriginProtocol: e.OriginProtocol.ValueString(),
		TLS:            endpointTLS{Profile: e.TLSProfile.ValueString()},
		Options:        options,
	}
	request.Certificate.Type = "CM"
	request.Certificate.Data.CM.ID = e.CertificateManagerID.ValueString()
	return request, nil
}

func (r *endpointResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan endpointModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	payload, err := plan.request()
	if err != nil {
		resp.Diagnostics.AddError("Build CDN endpoint request", err.Error())
		return
	}
	payload.FolderID = plan.FolderID.ValueString()
	payload.CNAME = plan.CNAME.ValueString()
	payload.ProviderType = plan.ProviderType.ValueString()
	payload.Origin = &endpointOrigin{GroupID: plan.OriginGroupID.ValueString()}
	body, err := r.client.modify(ctx, http.MethodPost, r.client.apiURL+"/resources", payload)
	if err != nil {
		resp.Diagnostics.AddError("Create CDN endpoint", err.Error())
		return
	}
	var identity struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(body, &identity); err != nil || !resourceIDPattern.MatchString(identity.ID) {
		resp.Diagnostics.AddError("Create CDN endpoint", "CDN operation did not return a resource ID.")
		return
	}
	plan.ID = types.StringValue(identity.ID)
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
}

func (r *endpointResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state endpointModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	result, err := r.client.readEndpoint(ctx, state.ID.ValueString())
	if errors.Is(err, httpStatusError(http.StatusNotFound)) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		resp.Diagnostics.AddError("Read CDN endpoint", err.Error())
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, &result)...)
}

func (c *cdnClient) readEndpoint(ctx context.Context, id string) (endpointModel, error) {
	var result apiEndpoint
	err := c.request(ctx, http.MethodGet, c.endpointURL(id), nil, &result)
	if err != nil {
		return endpointModel{}, err
	}
	if result.ID != id || result.CNAME == "" || result.Certificate.Type != "CM" || result.Certificate.Data.CM.ID == "" {
		return endpointModel{}, errors.New("CDN returned an incomplete endpoint or an unsupported certificate")
	}
	options, err := stateOptions(result.Options)
	if err != nil {
		return endpointModel{}, err
	}
	return endpointModel{
		ID:                   types.StringValue(result.ID),
		FolderID:             types.StringValue(result.FolderID),
		CNAME:                types.StringValue(result.CNAME),
		Active:               types.BoolValue(result.Active),
		ProviderType:         types.StringValue(result.ProviderType),
		OriginGroupID:        types.StringValue(result.OriginGroupID),
		OriginProtocol:       types.StringValue(result.OriginProtocol),
		CertificateManagerID: types.StringValue(result.Certificate.Data.CM.ID),
		TLSProfile:           types.StringValue(result.TLS.Profile),
		Options:              jsontypes.NewNormalizedValue(options),
	}, nil
}

func (r *endpointResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan endpointModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	payload, err := plan.request()
	if err != nil {
		resp.Diagnostics.AddError("Build CDN endpoint request", err.Error())
		return
	}
	payload.OriginGroupID = plan.OriginGroupID.ValueString()
	payload.UpdateMask = "active,originGroupId,originProtocol,sslCertificate,tls,options"
	_, err = r.client.modify(ctx, http.MethodPatch, r.client.endpointURL(plan.ID.ValueString()), payload)
	if err != nil {
		resp.Diagnostics.AddError("Update CDN endpoint", err.Error())
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
}

func (r *endpointResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state endpointModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	_, err := r.client.modify(ctx, http.MethodDelete, r.client.endpointURL(state.ID.ValueString()), nil)
	if err != nil && !errors.Is(err, httpStatusError(http.StatusNotFound)) {
		resp.Diagnostics.AddError("Delete CDN endpoint", err.Error())
	}
}

func (c *cdnClient) endpointURL(id string) string {
	return c.apiURL + "/resources/" + url.PathEscape(id)
}

func endpointOptions(text string) (map[string]json.RawMessage, error) {
	options, err := desiredOptions(text)
	if err != nil {
		return nil, err
	}
	// Signing and origin authentication are managed on rules only.
	if err := addSecrets(options, "", ""); err != nil {
		return nil, errors.New("configure signing and origin authentication on CDN rules")
	}
	return options, nil
}

type endpointOptionsValidator struct{}

func (endpointOptionsValidator) Description(context.Context) string {
	return "A JSON object of CDN endpoint options without signing or origin authentication."
}

func (v endpointOptionsValidator) MarkdownDescription(ctx context.Context) string {
	return v.Description(ctx)
}

func (endpointOptionsValidator) ValidateString(_ context.Context, req validator.StringRequest, resp *validator.StringResponse) {
	if req.ConfigValue.IsNull() || req.ConfigValue.IsUnknown() {
		return
	}
	if _, err := endpointOptions(req.ConfigValue.ValueString()); err != nil {
		resp.Diagnostics.AddAttributeError(req.Path, "Invalid endpoint options", err.Error())
	}
}
