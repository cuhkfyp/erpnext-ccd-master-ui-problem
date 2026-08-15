<template>
  <section class="card">
    <h2>{{ __('Access administration') }}</h2>
    <p class="muted">{{ __('Manage portal governance records. Client data is unavailable in this authority.') }}</p>
    <div class="tabs" aria-label="Administration resources">
      <button v-for="item in resources" :key="item.key" class="tab" :class="{ active: resource === item.key }" @click="select(item.key)">
        {{ item.label }}
      </button>
      <button class="tab" :class="{ active: resource === 'policies' }" @click="select('policies')">{{ __('Policies') }}</button>
      <button class="tab" :class="{ active: resource === 'coverage' }" @click="select('coverage')">{{ __('Coverage') }}</button>
    </div>

    <div v-if="error" class="error" role="alert">{{ error }}</div>
    <div v-if="message" class="success" role="status">{{ message }}</div>

    <template v-if="config">
      <h3>{{ config.label }}</h3>
      <p v-if="config.help" class="muted admin-help">{{ config.help }}</p>
      <form class="grid" @submit.prevent="saveResource">
        <div v-for="field in visibleFields" :key="field.name" class="field" :class="{'check-field':field.type === 'check'}">
          <template v-if="field.type === 'check'">
            <span class="field-label">{{ field.label }}</span>
            <label class="check-control" :for="`admin-${field.name}`"><input :id="`admin-${field.name}`" v-model="form[field.name]" type="checkbox" /><span>{{ field.checkLabel || __('Enabled') }}</span></label>
          </template>
          <template v-else>
            <label :for="`admin-${field.name}`">{{ field.label }}</label>
            <div v-if="field.type === 'multiselect'" :id="`admin-${field.name}`" class="multi-select-list" role="group" :aria-label="field.label">
              <label v-for="option in optionsFor(field)" :key="option.value" class="check-control">
                <input v-model="form[field.name]" type="checkbox" :value="option.value" :disabled="option.active === false" />
                <span>{{ option.label }}{{ option.active === false ? ` ${__('(inactive)')}` : '' }}</span>
              </label>
            </div>
            <select v-else-if="field.options || field.reference" :id="`admin-${field.name}`" v-model="form[field.name]" :required="field.required">
              <option value="">{{ field.placeholder || __('Select…') }}</option>
              <option v-for="option in optionsFor(field)" :key="option.value" :value="option.value">{{ option.label }}{{ option.active === false ? ` ${__('(inactive)')}` : '' }}</option>
            </select>
            <input v-else :id="`admin-${field.name}`" v-model="form[field.name]" :type="field.type || 'text'" :required="field.required" :placeholder="field.placeholder || ''" autocomplete="off" />
            <small v-if="field.help" class="field-help">{{ field.help }}</small>
          </template>
        </div>
        <div v-if="resource === 'source_profiles' && canActivatePolicy" class="field source-refresh">
          <label for="source-refresh-reason">{{ __('Reason for saving and refreshing') }}</label>
          <input id="source-refresh-reason" v-model="refreshReason" maxlength="500" autocomplete="off" required :placeholder="__('Required for the audit event')" />
          <small class="field-help">{{ __('Save and refresh is one operation. If every record cannot be indexed, the previous working assignment is kept.') }}</small>
        </div>
        <div class="actions"><button class="primary" :disabled="busy">{{ submitLabel }}</button><button v-if="editing" type="button" class="secondary" @click="reset">{{ __('Cancel') }}</button></div>
      </form>
      <div class="table-wrap" style="margin-top:1rem">
        <table class="data-table">
          <thead><tr><th v-for="field in config.columns" :key="field">{{ columnLabel(field) }}</th><th>{{ __('Action') }}</th></tr></thead>
          <tbody>
            <tr v-for="row in rows" :key="row.name">
              <td v-for="field in config.columns" :key="field">{{ formatCell(row, field) }}</td>
              <td><button class="secondary" @click="edit(row)">{{ __('Edit') }}</button> <button v-if="resource === 'source_profiles' && canActivatePolicy" class="secondary" :disabled="busy" @click="refreshSource(row)">{{ __('Refresh only') }}</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <template v-else-if="resource === 'policies'">
      <h3>{{ __('Draft immutable policy version') }}</h3>
      <div class="grid">
        <div class="field"><label for="policy-version">{{ __('Version') }}</label><input id="policy-version" v-model="policy.version" /></div>
        <div class="field"><label for="policy-title">{{ __('Title') }}</label><input id="policy-title" v-model="policy.title" /></div>
      </div>
      <div class="field" style="margin-top:.8rem">
        <label for="policy-fields">{{ __('Field rules (JSON array)') }}</label>
        <textarea id="policy-fields" v-model="policy.fields" spellcheck="false"></textarea>
      </div>
      <div class="actions"><button class="primary" :disabled="busy" @click="savePolicy">{{ policy.name ? __('Update draft') : __('Save draft') }}</button><button v-if="policy.name" class="secondary" :disabled="busy" @click="resetPolicy">{{ __('Cancel') }}</button></div>
      <div v-if="canActivatePolicy" class="field" style="margin-top:.8rem"><label for="activation-reason">{{ __('System Manager activation reason') }}</label><input id="activation-reason" v-model="activationReason" maxlength="500" autocomplete="off" /></div>
      <div class="table-wrap" style="margin-top:1rem"><table class="data-table"><thead><tr><th>{{ __('Version') }}</th><th>{{ __('Title') }}</th><th>{{ __('Status') }}</th><th>{{ __('Activated') }}</th><th>{{ __('Action') }}</th></tr></thead><tbody><tr v-for="row in policies" :key="row.name"><td><button class="policy-link" @click="viewPolicy(row)">{{ row.policy_version }}</button></td><td>{{ row.title }}</td><td>{{ row.status }}</td><td>{{ row.activated_on || '—' }}</td><td><button class="secondary" @click="viewPolicy(row)">{{ __('View details') }}</button> <button v-if="row.status==='Draft'" class="secondary" @click="editPolicy(row)">{{ __('Edit draft') }}</button> <button v-if="row.status==='Draft' && canActivatePolicy" class="secondary" :disabled="busy" @click="activatePolicy(row)">{{ __('Activate') }}</button></td></tr></tbody></table></div>
      <section v-if="viewingPolicy" class="policy-detail" aria-live="polite">
        <div class="actions" style="justify-content:space-between"><div><h3>{{ viewingPolicy.policy_version }} — {{ viewingPolicy.title }}</h3><p class="muted">{{ viewingPolicy.status }} · {{ viewingPolicy.fields.length }} {{ __('governed field rule(s)') }}</p></div><button class="secondary" @click="viewingPolicy=null">{{ __('Close details') }}</button></div>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>{{ __('Order') }}</th><th>{{ __('Portal label') }}</th><th>{{ __('CCD Master field') }}</th><th>{{ __('Classification') }}</th><th>{{ __('Mask') }}</th><th>{{ __('Search') }}</th><th>{{ __('Strong ID') }}</th><th>{{ __('Reveal') }}</th><th>{{ __('Correction') }}</th></tr></thead><tbody><tr v-for="field in viewingPolicy.fields" :key="field.fieldname"><td>{{ field.display_order }}</td><td>{{ field.label }}</td><td>{{ field.fieldname }}</td><td>{{ field.classification }}</td><td>{{ field.mask_strategy }}</td><td>{{ yesNo(field.searchable) }}</td><td>{{ yesNo(field.strong_identifier) }}</td><td>{{ field.reveal_authorities || '—' }}</td><td>{{ yesNo(field.correctable) }}</td></tr></tbody></table></div>
      </section>
      <p class="muted">{{ __('A System Manager must activate a draft with a reason. Activation retires the prior active version and requires a full index refresh.') }}</p>
    </template>

    <template v-else-if="resource === 'coverage'">
      <h3>{{ __('Centre and search-index coverage') }}</h3>
      <div v-if="coverage" class="grid">
        <div class="record-field"><small>{{ __('Total') }}</small><strong>{{ coverage.total_records }}</strong></div>
        <div class="record-field"><small>{{ __('Mapped') }}</small><strong>{{ coverage.mapped_records }}</strong></div>
        <div class="record-field"><small>{{ __('Unmapped') }}</small><strong>{{ coverage.unmapped_records }}</strong></div>
        <div class="record-field"><small>{{ __('Coverage') }}</small><strong>{{ coverage.coverage_percent }}%</strong></div>
      </div>
      <div v-if="coverage" class="table-wrap" style="margin-top:1rem"><table class="data-table"><thead><tr><th>{{ __('Source') }}</th><th>{{ __('Records') }}</th><th>{{ __('Mapped') }}</th></tr></thead><tbody><tr v-for="row in coverage.by_source" :key="row.source"><td>{{ row.source || __('Missing') }}</td><td>{{ row.source_records }}</td><td>{{ row.mapped_records }}</td></tr></tbody></table></div>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { call } from "../api";

const __ = (text) => text;
const { canActivatePolicy = false } = defineProps({ canActivatePolicy: Boolean });
const resources = [
  { key: "centres", label: __("Centres") }, { key: "aliases", label: __("Aliases") },
  { key: "source_profiles", label: __("Sources") }, { key: "profiles", label: __("Profiles") },
  { key: "grants", label: __("Grants") }, { key: "reveal_reasons", label: __("Reveal reasons") },
];
const configs = {
  centres: { label: __("Centres"), help: __("A centre is an access boundary. Its code becomes the exact value selected in grants."), columns: ["centre_code","centre_name","department","active"], fields: [{name:"centre_code",label:__("Code"),required:true,help:__("Stable governed code, for example 12345.")},{name:"centre_name",label:__("Name"),required:true},{name:"department",label:__("ERP Department"),reference:"departments",placeholder:__("No linked department")},{name:"active",label:__("Active"),type:"check"}] },
  aliases: { label: __("Centre aliases"), help: __("Use an alias only when a source centre value differs from the canonical centre code."), columns: ["alias_code","centre","source_profile","active"], fields: [{name:"alias_code",label:__("Source code"),required:true},{name:"centre",label:__("Canonical centre"),reference:"centres",required:true,placeholder:__("Select a centre")},{name:"source_profile",label:__("Source profile"),reference:"source_profiles",placeholder:__("All source profiles")},{name:"active",label:__("Active"),type:"check"}] },
  source_profiles: { label: __("Source centre assignments"), help: __("Choose the submitted registration, choose Fixed Centres, tick one or more centres, enter the audit reason, then use Save and refresh index once. Use Per-record Centre Key only when records in that registration belong to different centres."), columns: ["profile_code","source_registration","assignment_mode","fixed_centres_display","parser_type","active"], fields: [{name:"profile_code",label:__("Profile code"),required:true},{name:"source_registration",label:__("CCD Registration"),reference:"registrations",required:true,placeholder:__("Select a submitted registration")},{name:"assignment_mode",label:__("Centre assignment"),options:["Fixed Centres","Per-record Centre Key"],required:true,help:__("Fixed Centres applies every selected centre to every record. Per-record mode reads each record's hidden canonical centre key.")},{name:"fixed_centres",label:__("Fixed centre(s)"),reference:"centres",type:"multiselect",required:true,showWhen:{assignment_mode:"Fixed Centres"},help:__("Tick one for a single-centre source, or tick several only when every record is legitimately shared by all of them.")},{name:"parser_type",label:__("Per-record parser"),options:["Exact","Delimited","Regular Expression"],required:true,showWhen:{assignment_mode:"Per-record Centre Key"},help:__("Exact handles one key per record. Delimited handles a legitimately shared record containing multiple keys.")},{name:"delimiter",label:__("Delimiter"),showWhen:{assignment_mode:"Per-record Centre Key",parser_type:"Delimited"},placeholder:__("Comma by default"),help:__("Separates multiple centre codes stored in one record.")},{name:"parser_pattern",label:__("Bounded pattern"),showWhen:{assignment_mode:"Per-record Centre Key",parser_type:"Regular Expression"},required:true,placeholder:__("For example: ^[0-9]{5}$"),help:__("Required only for Regular Expression. Use Exact if no extraction is needed.")},{name:"active",label:__("Active"),type:"check"}] },
  profiles: { label: __("User profiles"), columns: ["user","authority","active"], fields: [{name:"user",label:__("Frappe user"),reference:"users",required:true,placeholder:__("Select an enabled system user")},{name:"authority",label:__("Exactly one authority"),options:["Reader","Operator","Data Steward","Access Administrator"],required:true},{name:"active",label:__("Active"),type:"check"}] },
  grants: { label: __("Explicit centre grants"), help: __("A grant joins one active portal profile to one canonical centre. Registration names are not centres."), columns: ["user","centre","active","effective_from","effective_to"], fields: [{name:"user",label:__("Portal user"),reference:"profiles",required:true,placeholder:__("Select a portal profile")},{name:"centre",label:__("Centre"),reference:"centres",required:true,placeholder:__("Select a canonical centre")},{name:"active",label:__("Active"),type:"check"},{name:"effective_from",label:__("From"),type:"date",help:__("Optional; blank means immediately.")},{name:"effective_to",label:__("To"),type:"date",help:__("Optional; blank means no scheduled expiry.")}] },
  reveal_reasons: { label: __("Reveal reasons"), columns: ["reason_code","label","display_order","active"], fields: [{name:"reason_code",label:__("Code")},{name:"label",label:__("Label")},{name:"display_order",label:__("Order"),type:"number"},{name:"active",label:__("Active"),type:"check"}] },
};
const resource = ref("centres"), rows = ref([]), policies = ref([]), coverage = ref(null), busy = ref(false), error = ref(""), message = ref(""), editing = ref(""), activationReason = ref(""), refreshReason = ref(""), viewingPolicy = ref(null);
const references = ref({centres:[],source_profiles:[],profiles:[],registrations:[],users:[],departments:[]});
const form = reactive({});
const policy = reactive({ name: "", version: "", title: "", fields: "[]" });
const config = computed(() => configs[resource.value]);
const visibleFields = computed(() => (config.value?.fields || []).filter((field) => !field.showWhen || Object.entries(field.showWhen).every(([key,value]) => form[key] === value)));
const submitLabel = computed(() => resource.value === "source_profiles" && canActivatePolicy ? (editing.value ? __("Save and refresh index") : __("Create and refresh index")) : (editing.value ? __("Update") : __("Create")));

function optionsFor(field) {
  if (field.options) return field.options.map((value) => ({value,label:__(value)}));
  return references.value[field.reference] || [];
}

function reset() { editing.value = ""; Object.keys(form).forEach((key) => delete form[key]); config.value?.fields.forEach((field) => form[field.name] = field.type === "check" ? true : field.type === "multiselect" ? [] : ""); if (resource.value === "source_profiles") { form.assignment_mode = "Per-record Centre Key"; form.parser_type = "Exact"; } }
function edit(row) { editing.value = row.name; reset(); editing.value = row.name; config.value.fields.forEach((field) => form[field.name] = field.type === "check" ? Boolean(row[field.name]) : field.type === "multiselect" ? [...(row[field.name] || [])] : (row[field.name] ?? "")); window.scrollTo({top:0,behavior:"smooth"}); }
function columnLabel(field) { if (field === "fixed_centres_display") return __("Fixed centre(s)"); return config.value?.fields.find((item) => item.name === field)?.label || field.replaceAll("_", " "); }
function formatCell(row, field) { if (resource.value === "source_profiles" && field === "parser_type" && row.assignment_mode === "Fixed Centres") return "—"; const value = row[field]; return Array.isArray(value) ? (value.join(", ") || "—") : (value ?? ""); }
async function select(value) { resource.value = value; error.value = ""; message.value = ""; viewingPolicy.value = null; reset(); await load(); }
async function load() { busy.value = true; try { if (config.value) rows.value = (await call("ccd_portal.admin.list_resources", {resource:resource.value}, false)).rows; else if (resource.value === "policies") policies.value = (await call("ccd_portal.admin.list_policies", {}, false)).policies; else coverage.value = await call("ccd_portal.admin.get_coverage", {}, false); } catch (e) { error.value = e.message; } finally { busy.value = false; } }
async function loadReferences() { references.value = await call("ccd_portal.admin.reference_options", {}, false); }
async function saveResource() {
  busy.value = true; error.value = "";
  try {
    const values = {...form};
    if (resource.value === "source_profiles") {
      values.fixed_centres = [...(values.fixed_centres || [])];
      if (values.assignment_mode === "Fixed Centres") {
        if (!values.fixed_centres.length) throw new Error(__("Select at least one fixed centre."));
        values.parser_type = "Exact"; values.delimiter = ""; values.parser_pattern = "";
      } else {
        values.fixed_centres = [];
        if (values.parser_type === "Exact") { values.delimiter = ""; values.parser_pattern = ""; }
        if (values.parser_type === "Delimited") values.parser_pattern = "";
        if (values.parser_type === "Regular Expression" && !String(values.parser_pattern || "").trim()) throw new Error(__("Enter a bounded pattern, or choose Exact when the whole value is the centre code."));
      }
    }
    if (resource.value === "source_profiles" && canActivatePolicy) {
      if (String(refreshReason.value || "").trim().length < 3) throw new Error(__("Enter a reason before saving and refreshing the source."));
      const result = await call("ccd_portal.admin.upsert_source_profile_and_refresh", {values,name:editing.value || null,reason:refreshReason.value});
      message.value = `${__("Source assignment saved, audited, and refreshed")}: ${result.refresh.indexed}/${result.refresh.total} ${__("indexed")}.`;
      refreshReason.value = "";
    } else {
      await call("ccd_portal.admin.upsert_resource", {resource:resource.value,values,name:editing.value || null});
      message.value = resource.value === "source_profiles" ? __("Source assignment saved. A System Manager must refresh its index before records are accessible.") : __("Governance record saved and audited.");
    }
    reset(); await Promise.all([load(),loadReferences()]);
  } catch(e) { error.value=e.message; } finally { busy.value=false; }
}
async function refreshSource(row) { busy.value=true; error.value=""; try { if (String(refreshReason.value || "").trim().length < 3) throw new Error(__("Enter a System Manager reason before refreshing the source index.")); const result=await call("ccd_portal.admin.refresh_index",{reason:refreshReason.value,source:row.source_registration}); refreshReason.value=""; message.value=`${__("Index refreshed")}: ${result.indexed}/${result.total} ${__("indexed")}, ${result.unmapped} ${__("unmapped")}, ${result.failed} ${__("failed")}.`; await load(); } catch(e) { error.value=e.message; } finally { busy.value=false; } }
function resetPolicy() { policy.name=""; policy.version=""; policy.title=""; policy.fields="[]"; }
function yesNo(value) { return value ? __("Yes") : __("No"); }
function viewPolicy(row) { viewingPolicy.value=row; requestAnimationFrame(()=>document.querySelector(".policy-detail")?.scrollIntoView({behavior:"smooth",block:"start"})); }
function editPolicy(row) { viewingPolicy.value=null; policy.name=row.name; policy.version=row.policy_version; policy.title=row.title; policy.fields=JSON.stringify(row.fields || [],null,2); window.scrollTo({top:0,behavior:"smooth"}); }
async function savePolicy() { busy.value=true; error.value=""; try { const fields=JSON.parse(policy.fields); await call("ccd_portal.admin.save_draft_policy",{policy_version:policy.version,title:policy.title,fields,name:policy.name || null}); message.value=__("Draft policy saved and audited."); resetPolicy(); await load(); } catch(e) { error.value=e.message; } finally { busy.value=false; } }
async function activatePolicy(row) { busy.value=true; error.value=""; try { await call("ccd_portal.admin.activate_policy",{policy_name:row.name,reason:activationReason.value}); activationReason.value=""; message.value=__("Policy activated and audited. Complete a full index refresh before enabling users."); await load(); } catch(e) { error.value=e.message; } finally { busy.value=false; } }
onMounted(async () => { reset(); busy.value=true; try { await Promise.all([loadReferences(),load()]); } catch(e) { error.value=e.message; } finally { busy.value=false; } });
</script>
