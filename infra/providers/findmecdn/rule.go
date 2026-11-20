package main

import (
	"context"
	"errors"
	"maps"
	"slices"

	"github.com/hashicorp/terraform-plugin-framework-jsontypes/jsontypes"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

type ruleResource struct {
	client *cdnClient
}

type ruleState struct {
	ID                  types.String         `tfsdk:"id"`
	ResourceID          types.String         `tfsdk:"resource_id"`
	Name                types.String         `tfsdk:"name"`
	Pattern             types.String         `tfsdk:"pattern"`
	Weight              types.String         `tfsdk:"weight"`
	OriginGroupID       types.String         `tfsdk:"origin_group_id"`
	OriginProtocol      types.String         `tfsdk:"origin_protocol"`
	OptionsJSON         jsontypes.Normalized `tfsdk:"options_json"`
	SecureKey           types.String         `tfsdk:"secure_key_wo"`
	OriginAuth          types.String         `tfsdk:"origin_auth_wo"`
	CredentialsRevision types.String         `tfsdk:"credentials_revision"`
	KeySourceRuleID     types.String         `tfsdk:"secure_key_source_rule_id"`
}

func (r *ruleResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_rule"
}

func (r *ruleResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "A path rule on an existing Yandex Cloud CDN endpoint.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{
				Computed:      true,
				PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()},
			},
			"resource_id": schema.StringAttribute{
				Required:      true,
				PlanModifiers: []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"name":            schema.StringAttribute{Required: true},
			"pattern":         schema.StringAttribute{Required: true},
			"weight":          schema.StringAttribute{Required: true},
			"origin_group_id": schema.StringAttribute{Optional: true},
			"origin_protocol": schema.StringAttribute{Optional: true},
			"options_json": schema.StringAttribute{
				Required:   true,
				CustomType: jsontypes.NormalizedType{},
				Validators: []validator.String{ruleOptionsValidator{}},
			},
			"secure_key_wo":  schema.StringAttribute{Optional: true, WriteOnly: true, Sensitive: true},
			"origin_auth_wo": schema.StringAttribute{Optional: true, WriteOnly: true, Sensitive: true},
			"secure_key_source_rule_id": schema.StringAttribute{
				Optional:    true,
				Description: "Rule on this CDN endpoint from which to obtain the signing key during apply.",
			},
			"credentials_revision": schema.StringAttribute{
				Optional:    true,
				Description: "Change this non-secret revision to reapply write-only credentials.",
			},
		},
	}
}

func (r *ruleResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	if req.ProviderData == nil {
		return
	}
	client, ok := req.ProviderData.(*cdnClient)
	if !ok {
		resp.Diagnostics.AddError("Configure CDN rule", "Unexpected provider configuration.")
		return
	}
	r.client = client
}

func (r *ruleResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resourceID, id, err := parseImportID(req.ID)
	if err != nil {
		resp.Diagnostics.AddError("Import CDN rule", err.Error())
		return
	}
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("resource_id"), resourceID)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), id)...)
}

func (r *ruleResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state ruleState
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	rule, found, err := r.client.read(ctx, state.ResourceID.ValueString(), state.ID.ValueString())
	if err != nil {
		resp.Diagnostics.AddError("Read CDN rule", err.Error())
		return
	}
	if !found {
		resp.State.RemoveResource(ctx)
		return
	}
	options, err := stateOptions(rule.Options)
	if err != nil {
		resp.Diagnostics.AddError("Read CDN rule options", err.Error())
		return
	}
	state.Name = types.StringValue(rule.Name)
	state.Pattern = types.StringValue(rule.Pattern)
	state.Weight = types.StringValue(rule.Weight)
	if rule.Weight == "" {
		state.Weight = types.StringValue("0")
	}
	state.OriginGroupID = optionalString(rule.OriginGroupID)
	if rule.OriginGroupID == "0" {
		state.OriginGroupID = types.StringNull()
	}
	state.OriginProtocol = optionalString(rule.OriginProtocol)
	state.OptionsJSON = jsontypes.NewNormalizedValue(options)
	resp.Diagnostics.Append(resp.State.Set(ctx, &state)...)
}

func optionalString(value string) types.String {
	if value == "" {
		return types.StringNull()
	}
	return types.StringValue(value)
}

func (r *ruleResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan ruleState
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	rule := r.writePayload(ctx, plan, req.Config, "", &resp.Diagnostics)
	if resp.Diagnostics.HasError() {
		return
	}
	id, err := r.client.create(ctx, rule)
	if err != nil {
		resp.Diagnostics.AddError("Create CDN rule", err.Error())
		return
	}
	plan.ID = types.StringValue(id)
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
}

func (r *ruleResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan ruleState
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	rule := r.writePayload(ctx, plan, req.Config, plan.ID.ValueString(), &resp.Diagnostics)
	if resp.Diagnostics.HasError() {
		return
	}
	var prior ruleState
	resp.Diagnostics.Append(req.State.Get(ctx, &prior)...)
	if resp.Diagnostics.HasError() {
		return
	}
	options, err := desiredOptions(prior.OptionsJSON.ValueString())
	if err != nil {
		resp.Diagnostics.AddError("Read prior CDN rule options", err.Error())
		return
	}
	if err := r.client.update(ctx, plan.ID.ValueString(), rule, slices.Collect(maps.Keys(options))); err != nil {
		resp.Diagnostics.AddError("Update CDN rule", err.Error())
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, &plan)...)
}

func (r *ruleResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state ruleState
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	if err := r.client.delete(ctx, state.ResourceID.ValueString(), state.ID.ValueString()); err != nil {
		resp.Diagnostics.AddError("Delete CDN rule", err.Error())
	}
}

func (r *ruleResource) writePayload(ctx context.Context, state ruleState, config tfsdk.Config, existingID string, diagnostics *diag.Diagnostics) apiRule {
	if diagnostics.HasError() {
		return apiRule{}
	}
	var signingKey, originAuth types.String
	diagnostics.Append(config.GetAttribute(ctx, path.Root("secure_key_wo"), &signingKey)...)
	diagnostics.Append(config.GetAttribute(ctx, path.Root("origin_auth_wo"), &originAuth)...)
	if diagnostics.HasError() {
		return apiRule{}
	}
	rule, err := state.apiRule()
	if err != nil {
		diagnostics.AddError("Prepare CDN rule", err.Error())
		return apiRule{}
	}
	err = r.client.setRuleCredentials(ctx, rule, existingID, state.KeySourceRuleID.ValueString(), signingKey.ValueString(), originAuth.ValueString())
	if err != nil {
		diagnostics.AddError("Prepare CDN rule credentials", err.Error())
	}
	return rule
}

func (state *ruleState) apiRule() (apiRule, error) {
	if !resourceIDPattern.MatchString(state.ResourceID.ValueString()) {
		return apiRule{}, errors.New("invalid CDN resource ID")
	}
	options, err := desiredOptions(state.OptionsJSON.ValueString())
	if err != nil {
		return apiRule{}, err
	}
	return apiRule{
		ResourceID:     state.ResourceID.ValueString(),
		Name:           state.Name.ValueString(),
		Pattern:        state.Pattern.ValueString(),
		Weight:         state.Weight.ValueString(),
		OriginGroupID:  state.OriginGroupID.ValueString(),
		OriginProtocol: state.OriginProtocol.ValueString(),
		Options:        options,
	}, nil
}

type ruleOptionsValidator struct{}

func (ruleOptionsValidator) Description(context.Context) string {
	return "A JSON object of supported CDN rule options without embedded credentials."
}

func (v ruleOptionsValidator) MarkdownDescription(ctx context.Context) string {
	return v.Description(ctx)
}

func (ruleOptionsValidator) ValidateString(_ context.Context, req validator.StringRequest, resp *validator.StringResponse) {
	if req.ConfigValue.IsNull() || req.ConfigValue.IsUnknown() {
		return
	}
	if _, err := desiredOptions(req.ConfigValue.ValueString()); err != nil {
		resp.Diagnostics.AddAttributeError(req.Path, "Invalid rule options", err.Error())
	}
}
