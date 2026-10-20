<template>
  <section class="card">
    <h2>{{ __('Access administration') }}</h2>
    <p class="muted">{{ __('Manage portal governance records. Client data is unavailable in this authority.') }}</p>
    <div class="tabs" aria-label="Administration resources">
      <button v-for="item in resources" :key="item.key" class="tab" :class="{ active: resource === item.key }" @click="select(item.key)">
        {{ item.label }}
      </button>
      <button class="tab" :class="{ active: resource === 'policies' }" @click="select('policies')">{{ __('Policies') }}</button>
      <button class="tab" :class="{ active: resource === 'rollout' }" @click="select('rollout')">{{ __('Same-person rollout') }}</button>
      <button class="tab" :class="{ active: resource === 'coverage' }" @click="select('coverage')">{{ __('Coverage') }}</button>
    </div>

    <div v-if="error" class="error" role="alert">{{ error }}</div>
    <div v-if="message" class="success" role="status">{{ message }}</div>
    <div v-if="warning" class="notice" role="status">{{ warning }}</div>
    <div v-if="sourceRefresh && ['Queued','Running'].includes(sourceRefresh.status)" class="notice" role="status">
      {{ __('Background index refresh') }}: {{ sourceRefresh.status }} · {{ sourceRefresh.processed_records || 0 }}/{{ sourceRefresh.total_records || 0 }} {{ __('records') }}
    </div>

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
            <template v-else-if="field.type === 'datalist'">
              <input :id="`admin-${field.name}`" v-model="form[field.name]" :list="`admin-${field.name}-options`" :required="field.required" :placeholder="field.placeholder || ''" autocomplete="off" />
              <datalist :id="`admin-${field.name}-options`"><option v-for="option in optionsFor(field)" :key="option.value" :value="option.value">{{ option.label }}</option></datalist>
            </template>
            <select v-else-if="field.options || field.reference" :id="`admin-${field.name}`" v-model="form[field.name]" :required="field.required">
              <option value="">{{ field.placeholder || __('Select…') }}</option>
              <option v-for="option in optionsFor(field)" :key="option.value" :value="option.value">{{ option.label }}{{ option.active === false ? ` ${__('(inactive)')}` : '' }}</option>
            </select>
            <input v-else :id="`admin-${field.name}`" v-model="form[field.name]" :type="field.type || 'text'" :required="field.required" :placeholder="field.placeholder || ''" autocomplete="off" />
            <small v-if="field.help" class="field-help">{{ field.help }}</small>
          </template>
        </div>
        <div v-if="resource === 'source_profiles' && canActivatePolicy" class="field source-refresh">
          <label for="source-refresh-reason">{{ __('Reason for source change or refresh') }}</label>
          <input id="source-refresh-reason" v-model="refreshReason" maxlength="500" autocomplete="off" required :placeholder="__('Required for the audit event')" />
          <small class="field-help">{{ __('Fixed centres save and refresh together. A changed per-record column enters a fail-closed sync gate until Complete sync and refresh succeeds.') }}</small>
        </div>
        <div class="actions"><button class="primary" :disabled="busy">{{ submitLabel }}</button><button v-if="editing" type="button" class="secondary" @click="reset">{{ __('Cancel') }}</button></div>
      </form>
      <div class="table-wrap" style="margin-top:1rem">
        <table class="data-table">
          <thead><tr><th v-for="field in config.columns" :key="field">{{ columnLabel(field) }}</th><th>{{ __('Action') }}</th></tr></thead>
          <tbody>
            <tr v-for="row in rows" :key="row.name">
              <td v-for="field in config.columns" :key="field">{{ formatCell(row, field) }}</td>
              <td><button class="secondary" :disabled="row.refresh_in_progress" @click="edit(row)">{{ __('Edit') }}</button> <button v-if="resource === 'source_profiles' && canActivatePolicy" class="secondary" :disabled="busy || (row.refresh_in_progress && !row.refresh_operation_token)" @click="refreshSource(row)">{{ sourceActionLabel(row) }}</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <template v-else-if="resource === 'policies'">
      <h3>{{ __('Clone active policy to governed V2 draft') }}</h3>
      <p class="muted">{{ __('This audited action preserves the active policy and adds the immutable Unified Person Number rule to a new draft.') }}</p>
      <div class="grid">
        <div class="field"><label for="v2-version">{{ __('New V2 version') }}</label><input id="v2-version" v-model="v2Clone.version" /></div>
        <div class="field"><label for="v2-title">{{ __('New V2 title') }}</label><input id="v2-title" v-model="v2Clone.title" /></div>
      </div>
      <div class="actions"><button class="secondary" :disabled="busy" @click="cloneV2">{{ __('Clone active policy to V2 draft') }}</button></div>
      <h3>{{ __('Draft immutable policy version') }}</h3>
      <div class="grid">
        <div class="field"><label for="policy-version">{{ __('Version') }}</label><input id="policy-version" v-model="policy.version" /></div>
        <div class="field"><label for="policy-title">{{ __('Title') }}</label><input id="policy-title" v-model="policy.title" /></div>
      </div>
      <label class="check-control" style="margin-top:.8rem"><input v-model="policy.same_person_enabled" type="checkbox" /><span>{{ __('Governed same-person capability') }}</span></label>
      <div class="field" style="margin-top:.8rem">
        <label for="policy-fields">{{ __('Field rules (JSON array)') }}</label>
        <textarea id="policy-fields" v-model="policy.fields" spellcheck="false"></textarea>
      </div>
      <div class="actions"><button class="primary" :disabled="busy" @click="savePolicy">{{ policy.name ? __('Update draft') : __('Save draft') }}</button><button v-if="policy.name" class="secondary" :disabled="busy" @click="resetPolicy">{{ __('Cancel') }}</button></div>
      <div v-if="canActivatePolicy" class="field" style="margin-top:.8rem"><label for="activation-reason">{{ __('System Manager activation reason') }}</label><input id="activation-reason" v-model="activationReason" maxlength="500" autocomplete="off" /></div>
      <div class="table-wrap" style="margin-top:1rem"><table class="data-table"><thead><tr><th>{{ __('Version') }}</th><th>{{ __('Title') }}</th><th>{{ __('Same-person') }}</th><th>{{ __('Status') }}</th><th>{{ __('Activated') }}</th><th>{{ __('Action') }}</th></tr></thead><tbody><tr v-for="row in policies" :key="row.name"><td><button class="policy-link" @click="viewPolicy(row)">{{ row.policy_version }}</button></td><td>{{ row.title }}</td><td>{{ yesNo(row.same_person_enabled) }}</td><td>{{ row.status }}</td><td>{{ row.activated_on || '—' }}</td><td><button class="secondary" @click="viewPolicy(row)">{{ __('View details') }}</button> <button v-if="row.status==='Draft'" class="secondary" @click="editPolicy(row)">{{ __('Edit draft') }}</button> <button v-if="row.status==='Draft' && canActivatePolicy" class="secondary" :disabled="busy" @click="activatePolicy(row)">{{ __('Activate') }}</button></td></tr></tbody></table></div>
      <section v-if="viewingPolicy" class="policy-detail" aria-live="polite">
        <div class="actions" style="justify-content:space-between"><div><h3>{{ viewingPolicy.policy_version }} — {{ viewingPolicy.title }}</h3><p class="muted">{{ viewingPolicy.status }} · {{ viewingPolicy.fields.length }} {{ __('governed field rule(s)') }}</p></div><button class="secondary" @click="viewingPolicy=null">{{ __('Close details') }}</button></div>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>{{ __('Order') }}</th><th>{{ __('Portal label') }}</th><th>{{ __('Source') }}</th><th>{{ __('CCD Master field / virtual field') }}</th><th>{{ __('Classification') }}</th><th>{{ __('Mask') }}</th><th>{{ __('Search') }}</th><th>{{ __('Strong ID') }}</th><th>{{ __('Reveal') }}</th><th>{{ __('Correction') }}</th></tr></thead><tbody><tr v-for="field in viewingPolicy.fields" :key="field.fieldname"><td>{{ field.display_order }}</td><td>{{ field.label }}</td><td>{{ field.source || 'CCD Master' }}</td><td>{{ field.fieldname }}</td><td>{{ field.classification }}</td><td>{{ field.mask_strategy }}</td><td>{{ yesNo(field.searchable) }}</td><td>{{ yesNo(field.strong_identifier) }}</td><td>{{ field.reveal_authorities || '—' }}</td><td>{{ yesNo(field.correctable) }}</td></tr></tbody></table></div>
      </section>
      <p class="muted">{{ __('A System Manager must activate a draft with a reason. Activation retires the prior active version and requires a full index refresh.') }}</p>
    </template>

    <template v-else-if="resource === 'rollout'">
      <h3>{{ __('Independent same-person rollout') }}</h3>
      <p class="muted">{{ __('Pilot users and the global gate are environment-owned. Neither setting grants portal or centre access.') }}</p>
      <div v-if="rollout" class="grid">
        <div class="record-field"><small>{{ __('Global state') }}</small><strong>{{ rollout.global_enabled ? __('Enabled') : __('Disabled') }}</strong></div>
        <div class="record-field"><small>{{ __('Identity schema') }}</small><strong>{{ rollout.identity_schema.ready ? __('Ready') : __('Not ready') }}</strong></div>
        <div class="record-field"><small>{{ __('Pilots') }}</small><strong>{{ rollout.pilot_users.length }}</strong></div>
        <div class="record-field"><small>{{ __('Missing service fallback') }}</small><strong>{{ rollout.service_name_fallback }}</strong></div>
      </div>
      <div class="field" style="margin-top:1rem"><span class="field-label">{{ __('Named pilot users') }}</span><div class="multi-select-list"><label v-for="option in clientProfileOptions" :key="option.value" class="check-control"><input v-model="rolloutPilots" type="checkbox" :value="option.value" /><span>{{ option.label }}</span></label></div></div>
      <div class="actions"><button class="secondary" :disabled="busy" @click="savePilots">{{ __('Save pilot users') }}</button></div>
      <div class="field" style="margin-top:1rem">
        <label for="service-name-fallback">{{ __('When a service name is missing') }}</label>
        <select id="service-name-fallback" v-model="rolloutServiceFallback" :disabled="!canActivatePolicy">
          <option value="Service not specified">{{ __('Show “Service not specified”') }}</option>
          <option value="CCD database/source name">{{ __('Show CCD database/source name') }}</option>
        </select>
        <small class="field-help">{{ __('The database/source option deliberately exposes the source name, but only after the record independently passes portal and centre authorization.') }}</small>
      </div>
      <template v-if="canActivatePolicy">
        <div class="field" style="margin-top:1rem"><label for="rollout-reason">{{ __('System Manager reason') }}</label><input id="rollout-reason" v-model="rolloutReason" maxlength="500" autocomplete="off" /></div>
        <div class="actions">
          <button class="secondary" :class="{ 'is-busy': busy }" :aria-busy="busy" :disabled="busy || rolloutServiceFallback === rollout?.service_name_fallback" @click="saveServiceFallback">{{ __('Save service fallback') }}</button>
          <button class="primary" :class="{ 'is-busy': busy }" :aria-busy="busy" :disabled="busy || rollout?.global_enabled" :title="globalEnableTitle" @click="setGlobalRollout(true)">{{ __('Enable globally') }}</button>
          <button class="secondary" :class="{ 'is-busy': busy }" :aria-busy="busy" :disabled="busy || !rollout?.global_enabled" :title="globalDisableTitle" @click="setGlobalRollout(false)">{{ __('Disable globally') }}</button>
        </div>
      </template>
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
      <template v-if="coverage?.same_person"><h3 style="margin-top:1.5rem">{{ __('Same-person readiness') }}</h3><div class="grid"><div class="record-field"><small>{{ __('Schema / indexes') }}</small><strong>{{ coverage.same_person.identity_schema.ready ? __('Ready') : __('Not ready') }}</strong></div><div class="record-field"><small>{{ __('Portal records assigned') }}</small><strong>{{ coverage.same_person.portal_records_with_active_unified_assignments }}</strong></div><div class="record-field"><small>{{ __('Eligible multi-record people') }}</small><strong>{{ coverage.same_person.eligible_multi_record_people }}</strong></div><div class="record-field"><small>{{ __('Revalidation suppressed') }}</small><strong>{{ coverage.same_person.revalidation_suppressed_people }}</strong></div><div class="record-field"><small>{{ __('Affected anomalies') }}</small><strong>{{ coverage.same_person.affected_anomalies }}</strong></div><div class="record-field"><small>{{ __('Last check') }}</small><strong>{{ coverage.same_person.last_check }}</strong></div></div></template>
    </template>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
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
  source_profiles: { label: __("Source centre assignments"), help: __("Select the current submitted CCD Registration. The portal separately derives the stable CCD source used by the agent, so Frappe amendment suffixes do not break indexing. Choose Fixed Centres when every source record has the same access centres; choose Per-record Centre Key when centre membership varies."), columns: ["profile_code","source_registration","canonical_source_id","assignment_mode","authoritative_centre_column","mapping_status","fixed_centres_display","parser_type","refresh_status","active"], fields: [{name:"profile_code",label:__("Profile code"),required:true},{name:"source_registration",label:__("Current submitted CCD Registration"),reference:"registrations",required:true,placeholder:__("Select a submitted registration"),help:__("Use the active amendment, for example a registration ending in -34. Its stable CCD source is derived automatically.")},{name:"assignment_mode",label:__("Centre assignment"),options:["Fixed Centres","Per-record Centre Key"],required:true,help:__("Fixed Centres applies every selected centre to every record. Per-record mode reads a governed centre column for each record.")},{name:"authoritative_centre_column",label:__("Authoritative centre source column"),reference:"registration_columns",type:"datalist",required:true,showWhen:{assignment_mode:"Per-record Centre Key"},placeholder:__("Type the exact database column name"),help:__("Choose a known mapped column or type an exact source column. Only letters, numbers, and underscores are accepted; the agent validates it during the next full sync.")},{name:"fixed_centres",label:__("Fixed centre(s)"),reference:"centres",type:"multiselect",required:true,showWhen:{assignment_mode:"Fixed Centres"},help:__("Tick one for a single-centre source, or tick several only when every record is legitimately shared by all of them.")},{name:"parser_type",label:__("Per-record parser"),options:["Exact","Delimited","Regular Expression"],required:true,showWhen:{assignment_mode:"Per-record Centre Key"},help:__("Exact handles one key per record. Delimited handles a legitimately shared record containing multiple keys.")},{name:"delimiter",label:__("Delimiter"),showWhen:{assignment_mode:"Per-record Centre Key",parser_type:"Delimited"},placeholder:__("Comma by default"),help:__("Separates multiple centre codes stored in one record.")},{name:"parser_pattern",label:__("Bounded pattern"),showWhen:{assignment_mode:"Per-record Centre Key",parser_type:"Regular Expression"},required:true,placeholder:__("For example: ^[0-9]{5}$"),help:__("Required only for Regular Expression. Use Exact if no extraction is needed.")},{name:"active",label:__("Active"),type:"check"}] },
  profiles: { label: __("User profiles"), columns: ["user","authority","active"], fields: [{name:"user",label:__("Frappe user"),reference:"users",required:true,placeholder:__("Select an enabled system user")},{name:"authority",label:__("Exactly one authority"),options:["Reader","Operator","Data Steward","Access Administrator"],required:true},{name:"active",label:__("Active"),type:"check"}] },
  grants: { label: __("Explicit centre grants"), help: __("A grant joins one active portal profile to one canonical centre. Registration names are not centres."), columns: ["user","centre","active","effective_from","effective_to"], fields: [{name:"user",label:__("Portal user"),reference:"profiles",required:true,placeholder:__("Select a portal profile")},{name:"centre",label:__("Centre"),reference:"centres",required:true,placeholder:__("Select a canonical centre")},{name:"active",label:__("Active"),type:"check"},{name:"effective_from",label:__("From"),type:"date",help:__("Optional; blank means immediately.")},{name:"effective_to",label:__("To"),type:"date",help:__("Optional; blank means no scheduled expiry.")}] },
  reveal_reasons: { label: __("Reveal reasons"), columns: ["reason_code","label","display_order","active"], fields: [{name:"reason_code",label:__("Code")},{name:"label",label:__("Label")},{name:"display_order",label:__("Order"),type:"number"},{name:"active",label:__("Active"),type:"check"}] },
};
const resource = ref("centres"), rows = ref([]), policies = ref([]), coverage = ref(null), rollout = ref(null), rolloutPilots = ref([]), rolloutServiceFallback = ref("Service not specified"), rolloutReason = ref(""), busy = ref(false), error = ref(""), message = ref(""), warning = ref(""), editing = ref(""), activationReason = ref(""), refreshReason = ref(""), viewingPolicy = ref(null), sourceRefresh = ref(null);
let sourceRefreshMonitor = 0;
const references = ref({centres:[],source_profiles:[],profiles:[],registrations:[],registration_columns:[],users:[],departments:[]});
const form = reactive({});
const policy = reactive({ name: "", version: "", title: "", same_person_enabled: false, fields: "[]" });
const v2Clone = reactive({version:"",title:""});
const config = computed(() => configs[resource.value]);
const clientProfileOptions = computed(() => (references.value.profiles || []).filter((option) => ["Reader","Operator","Data Steward"].includes(option.authority)));
const visibleFields = computed(() => (config.value?.fields || []).filter((field) => !field.showWhen || Object.entries(field.showWhen).every(([key,value]) => form[key] === value)));
const globalEnableTitle = computed(() => busy.value
  ? __("A governance request is in progress.")
  : rollout.value?.global_enabled
    ? __("Same-person search is already enabled globally.")
    : __("Enable same-person search for every otherwise eligible portal user."));
const globalDisableTitle = computed(() => busy.value
  ? __("A governance request is in progress.")
  : rollout.value?.global_enabled
    ? __("Disable global same-person search; named pilots remain independently eligible.")
    : __("Same-person search is already disabled globally."));
const submitLabel = computed(() => resource.value === "source_profiles" && canActivatePolicy ? (form.assignment_mode === "Per-record Centre Key" ? __("Save per-record configuration") : (editing.value ? __("Save and refresh index") : __("Create and refresh index"))) : (editing.value ? __("Update") : __("Create")));

function optionsFor(field) {
  if (field.options) return field.options.map((value) => ({value,label:__(value)}));
  if (field.reference === "registration_columns") return (references.value.registration_columns || []).filter((option) => option.registration === form.source_registration);
  return references.value[field.reference] || [];
}

function reset() { editing.value = ""; Object.keys(form).forEach((key) => delete form[key]); config.value?.fields.forEach((field) => form[field.name] = field.type === "check" ? true : field.type === "multiselect" ? [] : ""); if (resource.value === "source_profiles") { form.assignment_mode = "Per-record Centre Key"; form.parser_type = "Exact"; } }
function edit(row) { editing.value = row.name; reset(); editing.value = row.name; config.value.fields.forEach((field) => form[field.name] = field.type === "check" ? Boolean(row[field.name]) : field.type === "multiselect" ? [...(row[field.name] || [])] : (row[field.name] ?? "")); window.scrollTo({top:0,behavior:"smooth"}); }
function columnLabel(field) { if (field === "fixed_centres_display") return __("Fixed centre(s)"); if (field === "canonical_source_id") return __("Stable CCD source"); if (field === "refresh_status") return __("Index refresh"); return config.value?.fields.find((item) => item.name === field)?.label || field.replaceAll("_", " "); }
function formatCell(row, field) { if (resource.value === "source_profiles" && ((field === "parser_type" && row.assignment_mode === "Fixed Centres") || (field === "authoritative_centre_column" && row.assignment_mode === "Fixed Centres"))) return "—"; if (field === "refresh_status") { if (row.refresh_status) return `${row.refresh_status}${row.refresh_total ? ` ${row.refresh_processed || 0}/${row.refresh_total}` : ""}`; return row.sync_pending ? __("Pending") : __("Ready"); } const value = row[field]; return Array.isArray(value) ? (value.join(", ") || "—") : (value ?? ""); }
function sourceActionLabel(row) { if (row.refresh_in_progress) return row.refresh_operation_token ? __("View refresh progress") : __("Refresh in progress"); if (!row.sync_pending) return __("Refresh only"); return row.assignment_mode === "Per-record Centre Key" ? __("Complete sync and refresh") : __("Retry refresh"); }
function delay(milliseconds) { return new Promise((resolve) => window.setTimeout(resolve, milliseconds)); }
function updateSourceRefreshRow(operationToken, state) {
  const row = rows.value.find((item) => item.refresh_operation_token === operationToken);
  if (!row) return;
  Object.assign(row, {
    refresh_status: state.status,
    refresh_stage: state.stage || "",
    refresh_processed: Number(state.processed_records ?? row.refresh_processed ?? 0),
    refresh_total: Number(state.total_records ?? row.refresh_total ?? 0),
    refresh_in_progress: ["Queued","Running"].includes(state.status),
  });
}
function beginSourceRefresh(operation) { sourceRefresh.value = {...operation,processed_records:operation.processed_records || 0}; updateSourceRefreshRow(operation.operation_token, sourceRefresh.value); warning.value = `${__("Source saved and disabled while its index refresh runs in the background.")} ${__("You may leave this page and return to view progress.")}`; void monitorSourceRefresh(operation.operation_token); }
async function monitorSourceRefresh(operationToken) {
  const monitor = ++sourceRefreshMonitor;
  while (monitor === sourceRefreshMonitor) {
    await delay(2500);
    try {
      const state = await call("ccd_portal.source_refresh.get_source_refresh", {operation_token:operationToken}, false);
      if (monitor !== sourceRefreshMonitor) return;
      sourceRefresh.value = state;
      updateSourceRefreshRow(operationToken, state);
      if (["Queued","Running"].includes(state.status)) continue;
      if (state.status === "Completed") {
        warning.value = "";
        const result = state.result || {};
        message.value = `${__("Background index refresh completed")}: ${result.indexed || 0}/${result.total || 0} ${__("indexed")}.`;
        await load();
        return;
      }
      error.value = state.error || __("The background refresh has no confirmed result. The source remains disabled.");
      warning.value = "";
      await load();
      return;
    } catch (e) {
      error.value = e.message;
      return;
    }
  }
}
async function select(value) { resource.value = value; error.value = ""; message.value = ""; warning.value = ""; viewingPolicy.value = null; reset(); await load(); }
async function load() { busy.value = true; try { if (config.value) { rows.value = (await call("ccd_portal.admin.list_resources", {resource:resource.value}, false)).rows; const active = rows.value.find(row => row.refresh_in_progress && row.refresh_operation_token); if (active && sourceRefresh.value?.operation_token !== active.refresh_operation_token) void monitorSourceRefresh(active.refresh_operation_token); } else if (resource.value === "policies") policies.value = (await call("ccd_portal.admin.list_policies", {}, false)).policies; else if (resource.value === "rollout") { rollout.value=await call("ccd_portal.admin.get_same_person_rollout",{},false); rolloutPilots.value=[...rollout.value.pilot_users]; rolloutServiceFallback.value=rollout.value.service_name_fallback || "Service not specified"; } else coverage.value = await call("ccd_portal.admin.get_coverage", {}, false); } catch (e) { error.value = e.message; } finally { busy.value = false; } }
async function loadReferences() { references.value = await call("ccd_portal.admin.reference_options", {}, false); }
async function saveResource() {
  busy.value = true; error.value = ""; message.value = ""; warning.value = "";
  try {
    const values = {...form};
    let authoritativeCentreColumn = "";
    if (resource.value === "source_profiles") {
      authoritativeCentreColumn = String(values.authoritative_centre_column || "").trim();
      delete values.authoritative_centre_column;
      values.fixed_centres = [...(values.fixed_centres || [])];
      if (values.assignment_mode === "Fixed Centres") {
        if (!values.fixed_centres.length) throw new Error(__("Select at least one fixed centre."));
        values.parser_type = "Exact"; values.delimiter = ""; values.parser_pattern = "";
      } else {
        if (!authoritativeCentreColumn) throw new Error(__("Enter the authoritative centre source column."));
        values.fixed_centres = [];
        if (values.parser_type === "Exact") { values.delimiter = ""; values.parser_pattern = ""; }
        if (values.parser_type === "Delimited") values.parser_pattern = "";
        if (values.parser_type === "Regular Expression" && !String(values.parser_pattern || "").trim()) throw new Error(__("Enter a bounded pattern, or choose Exact when the whole value is the centre code."));
      }
    }
    if (resource.value === "source_profiles" && canActivatePolicy) {
      if (String(refreshReason.value || "").trim().length < 3) throw new Error(__("Enter a reason before changing the source."));
      const result = await call("ccd_portal.admin.configure_source", {values,name:editing.value || null,authoritative_centre_column:authoritativeCentreColumn,reason:refreshReason.value});
      if (result.sync_required) warning.value = `${__("Mapping saved and audited. Portal access for this source is disabled. Run the registration's existing full agent synchronization, then enter a new reason and click Complete sync and refresh.")} ${__("Centre keys")}: ${result.keyed_records}/${result.total_records}.`;
      else beginSourceRefresh(result.refresh_operation);
      refreshReason.value = "";
    } else {
      if (resource.value === "source_profiles" && values.assignment_mode === "Per-record Centre Key") throw new Error(__("A System Manager who is also the portal Access Administrator must save a submitted-registration centre mapping."));
      await call("ccd_portal.admin.upsert_resource", {resource:resource.value,values,name:editing.value || null});
      message.value = resource.value === "source_profiles" ? __("Source assignment saved. A System Manager must refresh its index before records are accessible.") : __("Governance record saved and audited.");
    }
    reset(); await Promise.all([load(),loadReferences()]);
  } catch(e) { error.value=e.message; } finally { busy.value=false; }
}
async function refreshSource(row) { busy.value=true; error.value=""; message.value=""; warning.value=""; try { if (row.refresh_in_progress && row.refresh_operation_token) { beginSourceRefresh({operation_token:row.refresh_operation_token,status:row.refresh_status,processed_records:row.refresh_processed,total_records:row.refresh_total}); return; } if (String(refreshReason.value || "").trim().length < 3) throw new Error(__("Enter a System Manager reason before refreshing the source index.")); const response=row.sync_pending && row.assignment_mode === "Per-record Centre Key" ? await call("ccd_portal.admin.complete_source_sync",{reason:refreshReason.value,source:row.source_registration}) : await call("ccd_portal.source_refresh.start_source_refresh",{reason:refreshReason.value,source:row.source_registration}); const operation=response.refresh_operation || response; refreshReason.value=""; beginSourceRefresh(operation); await load(); } catch(e) { error.value=e.message; } finally { busy.value=false; } }
function resetPolicy() { policy.name=""; policy.version=""; policy.title=""; policy.same_person_enabled=false; policy.fields="[]"; }
function yesNo(value) { return value ? __("Yes") : __("No"); }
function viewPolicy(row) { viewingPolicy.value=row; requestAnimationFrame(()=>document.querySelector(".policy-detail")?.scrollIntoView({behavior:"smooth",block:"start"})); }
function editPolicy(row) { viewingPolicy.value=null; policy.name=row.name; policy.version=row.policy_version; policy.title=row.title; policy.same_person_enabled=Boolean(row.same_person_enabled); policy.fields=JSON.stringify(row.fields || [],null,2); window.scrollTo({top:0,behavior:"smooth"}); }
async function savePolicy() { busy.value=true; error.value=""; try { const fields=JSON.parse(policy.fields); await call("ccd_portal.admin.save_draft_policy",{policy_version:policy.version,title:policy.title,same_person_enabled:policy.same_person_enabled?1:0,fields,name:policy.name || null}); message.value=__("Draft policy saved and audited."); resetPolicy(); await load(); } catch(e) { error.value=e.message; } finally { busy.value=false; } }
async function cloneV2() { busy.value=true; error.value=""; try { await call("ccd_portal.admin.clone_active_policy_to_v2",{policy_version:v2Clone.version,title:v2Clone.title}); v2Clone.version=""; v2Clone.title=""; message.value=__("Active policy cloned to an audited V2 draft."); await load(); } catch(e) { error.value=e.message; } finally { busy.value=false; } }
async function savePilots() { busy.value=true; error.value=""; try { await call("ccd_portal.admin.set_same_person_pilots",{users:[...rolloutPilots.value]}); message.value=__("Same-person pilots saved and audited."); await load(); } catch(e) { error.value=e.message; } finally { busy.value=false; } }
async function saveServiceFallback() { busy.value=true; error.value=""; try { await call("ccd_portal.admin.set_service_name_fallback",{fallback:rolloutServiceFallback.value,reason:rolloutReason.value}); rolloutReason.value=""; message.value=__("Service-name fallback saved and audited."); await load(); } catch(e) { error.value=e.message; } finally { busy.value=false; } }
async function setGlobalRollout(enabled) { busy.value=true; error.value=""; try { await call("ccd_portal.admin.set_same_person_global",{enabled:enabled?1:0,reason:rolloutReason.value}); rolloutReason.value=""; message.value=enabled?__("Same-person search enabled globally and audited."):__("Same-person search disabled globally and audited."); await load(); } catch(e) { error.value=e.message; } finally { busy.value=false; } }
async function activatePolicy(row) { busy.value=true; error.value=""; try { await call("ccd_portal.admin.activate_policy",{policy_name:row.name,reason:activationReason.value}); activationReason.value=""; message.value=__("Policy activated and audited. Complete a full index refresh before enabling users."); await load(); } catch(e) { error.value=e.message; } finally { busy.value=false; } }
onMounted(async () => { reset(); busy.value=true; try { await Promise.all([loadReferences(),load()]); } catch(e) { error.value=e.message; } finally { busy.value=false; } });
onBeforeUnmount(() => { sourceRefreshMonitor += 1; });
</script>
