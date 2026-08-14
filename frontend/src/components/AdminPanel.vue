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
      <form class="grid" @submit.prevent="saveResource">
        <div v-for="field in config.fields" :key="field.name" class="field">
          <label :for="`admin-${field.name}`">{{ field.label }}</label>
          <select v-if="field.options" :id="`admin-${field.name}`" v-model="form[field.name]">
            <option v-for="option in field.options" :key="option" :value="option">{{ option }}</option>
          </select>
          <input v-else-if="field.type === 'check'" :id="`admin-${field.name}`" v-model="form[field.name]" type="checkbox" />
          <input v-else :id="`admin-${field.name}`" v-model="form[field.name]" :type="field.type || 'text'" autocomplete="off" />
        </div>
        <div class="actions"><button class="primary" :disabled="busy">{{ editing ? __('Update') : __('Create') }}</button><button v-if="editing" type="button" class="secondary" @click="reset">{{ __('Cancel') }}</button></div>
      </form>
      <div class="table-wrap" style="margin-top:1rem">
        <table class="data-table">
          <thead><tr><th v-for="field in config.columns" :key="field">{{ field }}</th><th>{{ __('Action') }}</th></tr></thead>
          <tbody>
            <tr v-for="row in rows" :key="row.name">
              <td v-for="field in config.columns" :key="field">{{ row[field] }}</td>
              <td><button class="secondary" @click="edit(row)">{{ __('Edit') }}</button></td>
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
      <div class="table-wrap" style="margin-top:1rem"><table class="data-table"><thead><tr><th>{{ __('Version') }}</th><th>{{ __('Title') }}</th><th>{{ __('Status') }}</th><th>{{ __('Activated') }}</th><th>{{ __('Action') }}</th></tr></thead><tbody><tr v-for="row in policies" :key="row.name"><td>{{ row.policy_version }}</td><td>{{ row.title }}</td><td>{{ row.status }}</td><td>{{ row.activated_on || '—' }}</td><td><button v-if="row.status==='Draft'" class="secondary" @click="editPolicy(row)">{{ __('Edit draft') }}</button> <button v-if="row.status==='Draft' && canActivatePolicy" class="secondary" :disabled="busy" @click="activatePolicy(row)">{{ __('Activate') }}</button></td></tr></tbody></table></div>
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
  centres: { label: __("Centres"), columns: ["centre_code","centre_name","department","active"], fields: [{name:"centre_code",label:__("Code")},{name:"centre_name",label:__("Name")},{name:"department",label:__("ERP Department")},{name:"active",label:__("Active"),type:"check"}] },
  aliases: { label: __("Centre aliases"), columns: ["alias_code","centre","source_profile","active"], fields: [{name:"alias_code",label:__("Source code")},{name:"centre",label:__("Canonical centre")},{name:"source_profile",label:__("Source profile")},{name:"active",label:__("Active"),type:"check"}] },
  source_profiles: { label: __("Source profiles"), columns: ["profile_code","source_registration","parser_type","active"], fields: [{name:"profile_code",label:__("Profile code")},{name:"source_registration",label:__("CCD Registration")},{name:"parser_type",label:__("Parser"),options:["Exact","Delimited","Regular Expression"]},{name:"delimiter",label:__("Delimiter")},{name:"parser_pattern",label:__("Pattern")},{name:"active",label:__("Active"),type:"check"}] },
  profiles: { label: __("User profiles"), columns: ["user","authority","active"], fields: [{name:"user",label:__("Frappe user"),type:"email"},{name:"authority",label:__("Exactly one authority"),options:["Reader","Operator","Data Steward","Access Administrator"]},{name:"active",label:__("Active"),type:"check"}] },
  grants: { label: __("Explicit centre grants"), columns: ["user","centre","active","effective_from","effective_to"], fields: [{name:"user",label:__("Frappe user"),type:"email"},{name:"centre",label:__("Centre")},{name:"active",label:__("Active"),type:"check"},{name:"effective_from",label:__("From"),type:"date"},{name:"effective_to",label:__("To"),type:"date"}] },
  reveal_reasons: { label: __("Reveal reasons"), columns: ["reason_code","label","display_order","active"], fields: [{name:"reason_code",label:__("Code")},{name:"label",label:__("Label")},{name:"display_order",label:__("Order"),type:"number"},{name:"active",label:__("Active"),type:"check"}] },
};
const resource = ref("centres"), rows = ref([]), policies = ref([]), coverage = ref(null), busy = ref(false), error = ref(""), message = ref(""), editing = ref(""), activationReason = ref("");
const form = reactive({});
const policy = reactive({ name: "", version: "", title: "", fields: "[]" });
const config = computed(() => configs[resource.value]);

function reset() { editing.value = ""; Object.keys(form).forEach((key) => delete form[key]); config.value?.fields.forEach((field) => form[field.name] = field.type === "check" ? true : ""); }
function edit(row) { editing.value = row.name; reset(); editing.value = row.name; config.value.fields.forEach((field) => form[field.name] = row[field.name] ?? (field.type === "check")); window.scrollTo({top:0,behavior:"smooth"}); }
async function select(value) { resource.value = value; error.value = ""; message.value = ""; reset(); await load(); }
async function load() { busy.value = true; try { if (config.value) rows.value = (await call("ccd_portal.admin.list_resources", {resource:resource.value}, false)).rows; else if (resource.value === "policies") policies.value = (await call("ccd_portal.admin.list_policies", {}, false)).policies; else coverage.value = await call("ccd_portal.admin.get_coverage", {}, false); } catch (e) { error.value = e.message; } finally { busy.value = false; } }
async function saveResource() { busy.value = true; error.value = ""; try { await call("ccd_portal.admin.upsert_resource", {resource:resource.value,values:{...form},name:editing.value || null}); message.value = __("Governance record saved and audited."); reset(); await load(); } catch(e) { error.value=e.message; } finally { busy.value=false; } }
function resetPolicy() { policy.name=""; policy.version=""; policy.title=""; policy.fields="[]"; }
function editPolicy(row) { policy.name=row.name; policy.version=row.policy_version; policy.title=row.title; policy.fields=JSON.stringify(row.fields || [],null,2); window.scrollTo({top:0,behavior:"smooth"}); }
async function savePolicy() { busy.value=true; error.value=""; try { const fields=JSON.parse(policy.fields); await call("ccd_portal.admin.save_draft_policy",{policy_version:policy.version,title:policy.title,fields,name:policy.name || null}); message.value=__("Draft policy saved and audited."); resetPolicy(); await load(); } catch(e) { error.value=e.message; } finally { busy.value=false; } }
async function activatePolicy(row) { busy.value=true; error.value=""; try { await call("ccd_portal.admin.activate_policy",{policy_name:row.name,reason:activationReason.value}); activationReason.value=""; message.value=__("Policy activated and audited. Complete a full index refresh before enabling users."); await load(); } catch(e) { error.value=e.message; } finally { busy.value=false; } }
onMounted(async () => { reset(); await load(); });
</script>
