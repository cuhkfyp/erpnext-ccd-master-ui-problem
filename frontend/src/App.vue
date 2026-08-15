<template>
  <div class="shell">
    <header class="topbar">
      <div class="brand"><div class="brand-mark">CCD</div><div><h1>{{ __('Master Staff Portal') }}</h1><p>{{ __('Governed targeted access') }}</p></div></div>
      <div v-if="boot" class="account"><span>{{ boot.user.display_name }}</span><FrappeBadge :label="boot.user.authority" theme="green" /></div>
    </header>
    <main class="layout">
      <div v-if="boot?.preview_mode" class="notice">{{ __('Administrator preview — the staff feature flag is disabled.') }}</div>
      <div v-if="error && !unauthenticated" class="error" role="alert">{{ error }} <a href="/app">{{ __('Return to Desk') }}</a></div>
      <div v-if="message" class="success" role="status">{{ message }}</div>
      <section v-if="unauthenticated" class="card guest-card">
        <h2>{{ __('Sign in required') }}</h2>
        <p class="muted">{{ __('Please sign in to access the CCD Staff Portal.') }}</p>
        <div class="actions"><a class="primary button-link" href="/login?redirect-to=%2Fccd-portal">{{ __('Sign in') }}</a></div>
      </section>
      <section v-else-if="loading" class="card"><p>{{ __('Loading governed access profile…') }}</p></section>
      <AdminPanel v-else-if="boot?.is_access_administrator" :can-activate-policy="boot.can_activate_policy" />
      <template v-else-if="boot">
        <nav class="tabs" aria-label="Portal sections">
          <button class="tab" :class="{active:view==='search'}" @click="view='search'">{{ __('Targeted search') }}</button>
          <button v-if="canCorrect" class="tab" :class="{active:view==='corrections'}" @click="openCorrections">{{ boot.user.authority === 'Data Steward' ? __('Correction queue') : __('My corrections') }}</button>
        </nav>

        <template v-if="view === 'search'">
          <section class="card">
            <h2>{{ __('Find a client record') }}</h2>
            <p class="muted">{{ __('Use one policy-approved exact identifier, or combine a name with date of birth. Browse and fuzzy search are unavailable.') }}</p>
            <form @submit.prevent="runSearch">
              <div class="grid">
                <div v-for="(criterion,index) in criteria" :key="index" class="field">
                  <label :for="`criteria-${index}`">{{ __('Search field') }}</label>
                  <select :id="`criteria-${index}`" v-model="criterion.fieldname" required>
                    <option value="" disabled>{{ __('Select a field') }}</option>
                    <option v-for="field in searchFields" :key="field.fieldname" :value="field.fieldname">{{ field.label }}</option>
                  </select>
                  <input v-model="criterion.value" :type="inputType(criterion.fieldname)" :aria-label="__('Exact value')" autocomplete="off" required />
                  <button v-if="criteria.length > 1" type="button" class="secondary" @click="criteria.splice(index,1)">{{ __('Remove') }}</button>
                </div>
              </div>
              <div class="actions">
                <button type="button" class="secondary" :disabled="criteria.length >= 5" @click="criteria.push({fieldname:'',value:''})">{{ __('Add criterion') }}</button>
                <button class="primary" :disabled="busy">{{ busy ? __('Searching…') : __('Search') }}</button>
              </div>
            </form>
          </section>

          <div v-if="searched && !results.length" class="card"><h3>{{ __('No accessible records found') }}</h3><p class="muted">{{ __('Check the exact criteria. Results outside your explicit centre grants are intentionally indistinguishable from no match.') }}</p></div>
          <section v-if="results.length" aria-live="polite">
            <p class="muted">{{ results.length }} {{ __('masked result(s); maximum 20') }}</p>
            <div class="results">
              <article v-for="record in results" :key="record.id" class="card result" tabindex="0" @click="openDetail(record.id)" @keydown.enter="openDetail(record.id)">
                <div><span v-for="centre in record.centres" :key="centre" class="pill">{{ centre }}</span></div>
                <div class="record-fields"><div v-for="field in record.fields.slice(0,6)" :key="field.fieldname" class="record-field"><small>{{ field.label }}</small><strong>{{ field.value || '—' }}</strong></div></div>
              </article>
            </div>
          </section>

          <section v-if="selected" class="card" style="margin-top:1rem">
            <div class="actions" style="justify-content:space-between;margin-top:0"><div><h2 style="margin-bottom:.2rem">{{ __('Masked record detail') }}</h2><span v-for="centre in selected.centres" :key="centre" class="pill">{{ centre }}</span></div><button class="secondary" @click="clearSelected">{{ __('Close') }}</button></div>
            <nav class="tabs detail-tabs" aria-label="Record detail sections">
              <button class="tab" :class="{active:detailSection==='details'}" @click="detailSection='details'">{{ __('Details') }}</button>
              <button v-if="contactFields.length" class="tab" :class="{active:detailSection==='contact'}" @click="detailSection='contact'">{{ __('Contact Information') }}</button>
            </nav>
            <div v-if="detailSection === 'details'" class="record-fields" style="margin-top:1rem"><div v-for="field in detailFields" :key="field.fieldname" class="record-field"><small>{{ field.label }}</small><strong>{{ field.value || '—' }}</strong></div></div>
            <template v-else>
              <section v-for="group in contactGroups" :key="group.label" class="detail-group">
                <h3>{{ group.label }}</h3>
                <div class="record-fields"><div v-for="field in group.fields" :key="field.fieldname" class="record-field"><small>{{ field.label }}</small><strong>{{ field.value || '—' }}</strong></div></div>
              </section>
            </template>
            <p v-if="revealed" class="notice" style="margin-top:1rem">{{ __('Revealed values clear automatically in') }} {{ revealSeconds }}s. {{ __('Do not copy them into notes or browser storage.') }}</p>
            <div class="actions"><button v-if="canReveal" class="primary" @click="showReveal=true">{{ __('Temporarily reveal eligible fields') }}</button><button v-if="canCorrect" class="secondary" @click="openCorrection">{{ __('Request a correction') }}</button></div>
          </section>
        </template>

        <section v-else class="card">
          <h2>{{ boot.user.authority === 'Data Steward' ? __('Scoped correction queue') : __('My correction requests') }}</h2>
          <p class="muted">{{ __('Approved changes must be applied manually in the authoritative source. Later synchronization reconciles the request.') }}</p>
          <div class="table-wrap"><table class="data-table"><thead><tr><th>{{ __('Request') }}</th><th>{{ __('Record') }}</th><th>{{ __('Fields') }}</th><th>{{ __('Reason') }}</th><th>{{ __('Status') }}</th><th v-if="boot.user.authority==='Data Steward'">{{ __('Action') }}</th></tr></thead><tbody><tr v-for="row in correctionRows" :key="row.name"><td>{{ row.name }}</td><td>{{ row.portal_record_id }}</td><td>{{ row.changed_fields }}</td><td>{{ row.reason }}</td><td>{{ row.status }}</td><td v-if="boot.user.authority==='Data Steward'"><button v-if="row.status==='Proposed'" class="secondary" @click="openDecision(row)">{{ __('Review') }}</button></td></tr></tbody></table></div>
        </section>
      </template>
    </main>

    <div v-if="showReveal" class="modal-backdrop" @click.self="showReveal=false"><form class="modal" @submit.prevent="doReveal"><h2>{{ __('Reason-based PII reveal') }}</h2><div class="field"><label for="reason-code">{{ __('Approved reason') }}</label><select id="reason-code" v-model="revealForm.reason_code" required><option value="" disabled>{{ __('Select a reason') }}</option><option v-for="reason in boot.reveal_reasons" :key="reason.reason_code" :value="reason.reason_code">{{ reason.label }}</option></select></div><div class="field" style="margin-top:.8rem"><label for="reveal-context">{{ __('Optional context') }}</label><textarea id="reveal-context" v-model="revealForm.context_note" maxlength="500"></textarea></div><div class="actions"><button class="primary" :disabled="busy">{{ __('Reveal temporarily') }}</button><button type="button" class="secondary" @click="showReveal=false">{{ __('Cancel') }}</button></div></form></div>

    <div v-if="showCorrection" class="modal-backdrop" @click.self="showCorrection=false"><form class="modal" @submit.prevent="submitCorrection"><h2>{{ __('Correction request') }}</h2><p class="muted">{{ __('This does not modify CCD Master. A different scoped Data Steward must decide it.') }}</p><div v-if="selected?.centres.length > 1" class="field"><label for="correction-centre">{{ __('Approval centre') }}</label><select id="correction-centre" v-model="correctionForm.centre" required><option value="" disabled>{{ __('Select the centre responsible for this request') }}</option><option v-for="centre in selected.centres" :key="centre" :value="centre">{{ centre }}</option></select></div><div v-for="field in correctableFields" :key="field.fieldname" class="field" style="margin-top:.7rem"><label><input v-model="correctionForm.selected[field.fieldname]" type="checkbox" /> {{ __('Propose a change to') }} {{ field.label }}</label><input v-if="correctionForm.selected[field.fieldname]" :id="`change-${field.fieldname}`" v-model="correctionForm.changes[field.fieldname]" autocomplete="off" :aria-label="field.label" /></div><div class="field" style="margin-top:.8rem"><label for="correction-reason">{{ __('Reason') }}</label><textarea id="correction-reason" v-model="correctionForm.reason" maxlength="500" required></textarea></div><div class="actions"><button class="primary" :disabled="busy">{{ __('Submit request') }}</button><button type="button" class="secondary" @click="showCorrection=false">{{ __('Cancel') }}</button></div></form></div>

    <div v-if="decisionRequest" class="modal-backdrop" @click.self="closeDecision"><form class="modal" @submit.prevent="submitDecision"><h2>{{ __('Steward decision') }}</h2><p>{{ decisionRequest.name }}</p><template v-if="!decisionReview"><div class="field"><label for="review-reason">{{ __('Approved reveal reason') }}</label><select id="review-reason" v-model="reviewForm.reason_code" required><option value="" disabled>{{ __('Select a reason') }}</option><option v-for="reason in boot.reveal_reasons" :key="reason.reason_code" :value="reason.reason_code">{{ reason.label }}</option></select></div><div class="field" style="margin-top:.8rem"><label for="review-context">{{ __('Optional context') }}</label><textarea id="review-context" v-model="reviewForm.context_note" maxlength="500"></textarea></div><div class="actions"><button type="button" class="primary" :disabled="busy" @click="loadCorrectionReview">{{ __('Reveal comparison temporarily') }}</button><button type="button" class="secondary" @click="closeDecision">{{ __('Cancel') }}</button></div></template><template v-else><p class="notice">{{ __('Sensitive comparison clears automatically. Decide only after checking the authoritative source workflow.') }}</p><div class="table-wrap"><table class="data-table"><thead><tr><th>{{ __('Field') }}</th><th>{{ __('Current') }}</th><th>{{ __('Proposed') }}</th></tr></thead><tbody><tr v-for="row in decisionReview.comparison" :key="row.fieldname"><td>{{ row.label }}</td><td>{{ row.current_value || '—' }}</td><td>{{ row.proposed_value || '—' }}</td></tr></tbody></table></div><div class="field" style="margin-top:.8rem"><label for="decision">{{ __('Decision') }}</label><select id="decision" v-model="decisionForm.decision"><option>Approve</option><option>Reject</option></select></div><div class="field" style="margin-top:.8rem"><label for="decision-reason">{{ __('Decision reason') }}</label><textarea id="decision-reason" v-model="decisionForm.reason" maxlength="500" required></textarea></div><div class="actions"><button class="primary" :disabled="busy">{{ __('Record decision') }}</button><button type="button" class="secondary" @click="closeDecision">{{ __('Cancel') }}</button></div></template></form></div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { call } from "./api";
import AdminPanel from "./components/AdminPanel.vue";

const __ = (text) => text;
const sessionAuthenticated = Boolean(window.ccd_portal_authenticated);
const loading = ref(true), busy = ref(false), error = ref(""), message = ref(""), boot = ref(null), unauthenticated = ref(!sessionAuthenticated), view = ref("search"), detailSection = ref("details");
const criteria = reactive([{fieldname:"",value:""}]), results = ref([]), searched = ref(false), selected = ref(null), revealed = ref(null), revealSeconds = ref(0), correctionRows = ref([]);
const showReveal = ref(false), showCorrection = ref(false), decisionRequest = ref(null), decisionReview = ref(null);
const revealForm = reactive({reason_code:"",context_note:""});
const correctionForm = reactive({changes:{},selected:{},reason:"",centre:""});
const decisionForm = reactive({decision:"Approve",reason:""});
const reviewForm = reactive({reason_code:"",context_note:""});
let revealTimer = null, countdownTimer = null, reviewTimer = null;
const searchFields = computed(() => boot.value?.policy?.fields?.filter((field) => field.searchable) || []);
const canReveal = computed(() => ["Operator","Data Steward"].includes(boot.value?.user.authority));
const canCorrect = computed(() => ["Operator","Data Steward"].includes(boot.value?.user.authority));
const correctableFields = computed(() => selected.value?.fields.filter((field) => field.correctable) || []);
const displayFields = computed(() => { if (!selected.value) return []; const raw = Object.fromEntries((revealed.value?.fields || []).map((field) => [field.fieldname,field.value])); return selected.value.fields.map((field) => ({...field,value:Object.hasOwn(raw,field.fieldname)?raw[field.fieldname]:field.value})); });
const contactFields = computed(() => displayFields.value.filter((field) => field.classification === "Contact"));
const detailFields = computed(() => displayFields.value.filter((field) => field.classification !== "Contact"));
const contactGroups = computed(() => [
  {label:__("Residential address"),fields:contactFields.value.filter((field)=>field.fieldname.startsWith("res_"))},
  {label:__("Postal address"),fields:contactFields.value.filter((field)=>field.fieldname === "pos_country" || field.fieldname.startsWith("post_"))},
  {label:__("Phone and email"),fields:contactFields.value.filter((field)=>["phone_num","mobile","email"].includes(field.fieldname))},
  {label:__("Contact persons"),fields:contactFields.value.filter((field)=>field.fieldname.startsWith("contact"))},
].filter((group)=>group.fields.length));
function inputType(fieldname) { const kind=searchFields.value.find((f)=>f.fieldname===fieldname)?.data_kind; return kind==="Date"?"date":kind==="Email"?"email":"text"; }
function clearReveal() { revealed.value=null; revealSeconds.value=0; if(revealTimer) clearTimeout(revealTimer); if(countdownTimer) clearInterval(countdownTimer); revealTimer=null; countdownTimer=null; }
function clearSelected() { clearReveal(); selected.value=null; detailSection.value="details"; }
function setError(e) { error.value=e.message || __("The request could not be completed."); unauthenticated.value=e.status===401 || (!sessionAuthenticated && e.status===403); }
async function runSearch() { busy.value=true; error.value=""; message.value=""; clearSelected(); try { const data=await call("ccd_portal.api.search",{criteria:criteria.map((row)=>({...row}))}); results.value=data.results; searched.value=true; criteria.forEach((row)=>row.value=""); } catch(e){setError(e);} finally{busy.value=false;} }
async function openDetail(id) { busy.value=true; error.value=""; clearReveal(); detailSection.value="details"; try { selected.value=await call("ccd_portal.api.detail",{record_id:id}); selected.value && window.scrollTo({top:document.body.scrollHeight,behavior:"smooth"}); } catch(e){setError(e);} finally{busy.value=false;} }
async function doReveal() { busy.value=true; error.value=""; try { revealed.value=await call("ccd_portal.api.reveal",{record_id:selected.value.id,...revealForm}); revealSeconds.value=revealed.value.expires_in; showReveal.value=false; revealForm.reason_code=""; revealForm.context_note=""; countdownTimer=setInterval(()=>revealSeconds.value=Math.max(0,revealSeconds.value-1),1000); revealTimer=setTimeout(clearReveal,revealed.value.expires_in*1000); } catch(e){setError(e);} finally{busy.value=false;} }
function openCorrection() { correctionForm.changes={}; correctionForm.selected={}; correctionForm.reason=""; correctionForm.centre=selected.value?.centres.length===1?selected.value.centres[0]:""; showCorrection.value=true; }
async function submitCorrection() { const changes=Object.fromEntries(Object.keys(correctionForm.selected).filter((fieldname)=>correctionForm.selected[fieldname]).map((fieldname)=>[fieldname,correctionForm.changes[fieldname] ?? ""])); busy.value=true; error.value=""; try { const response=await call("ccd_portal.api.submit_correction",{record_id:selected.value.id,changes,reason:correctionForm.reason,centre:correctionForm.centre}); showCorrection.value=false; message.value=`${__("Correction request submitted")}: ${response.request_id}`; } catch(e){setError(e);} finally{busy.value=false;} }
async function openCorrections() { view.value="corrections"; busy.value=true; error.value=""; try { correctionRows.value=(await call("ccd_portal.api.corrections",{},false)).requests; } catch(e){setError(e);} finally{busy.value=false;} }
function openDecision(row) { decisionRequest.value=row; decisionReview.value=null; reviewForm.reason_code=""; reviewForm.context_note=""; decisionForm.reason=""; }
function closeDecision() { decisionRequest.value=null; decisionReview.value=null; if(reviewTimer) clearTimeout(reviewTimer); reviewTimer=null; }
async function loadCorrectionReview() { busy.value=true; error.value=""; try { decisionReview.value=await call("ccd_portal.api.correction_detail",{request_id:decisionRequest.value.name,...reviewForm}); reviewTimer=setTimeout(closeDecision,decisionReview.value.expires_in*1000); } catch(e){setError(e);} finally{busy.value=false;} }
async function submitDecision() { if(!decisionReview.value)return; busy.value=true; error.value=""; try { await call("ccd_portal.api.decide_correction",{request_id:decisionRequest.value.name,...decisionForm}); closeDecision(); decisionForm.reason=""; message.value=__("Decision recorded and audited."); await openCorrections(); } catch(e){setError(e);} finally{busy.value=false;} }
function visibilityChanged() { if(document.hidden) { clearReveal(); closeDecision(); } }
onMounted(async()=>{ if(!sessionAuthenticated){loading.value=false;return;} document.addEventListener("visibilitychange",visibilityChanged); try{boot.value=await call("ccd_portal.api.bootstrap",{},false);}catch(e){setError(e);}finally{loading.value=false;} });
onBeforeUnmount(()=>{clearReveal();closeDecision();document.removeEventListener("visibilitychange",visibilityChanged);});
</script>
