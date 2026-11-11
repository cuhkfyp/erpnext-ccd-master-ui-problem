const FIRST_NAME_FIELDS = new Set(["eng_firstname", "chi_firstname"]);
const GUIDANCE_FIXED_BY_ANOTHER_CRITERION = new Set([
  "date-needs-name",
  "english-surname-needs-companion",
  "chinese-surname-needs-companion",
  "field-needs-companion",
  "unsupported-combination",
]);

function selectedFields(criteria, searchFields) {
  const byName = new Map(searchFields.map((field) => [field.fieldname, field]));
  return criteria.map((row) => byName.get(row.fieldname)).filter(Boolean);
}

export function searchFieldRequiresCompanion(field) {
  if (!field || field.source === "Unified Person" || field.strong_identifier) return false;
  return !FIRST_NAME_FIELDS.has(field.fieldname);
}

export function isAllowedSearchShape(criteria, searchFields) {
  const fields = selectedFields(criteria, searchFields);
  if (!fields.length || fields.length !== criteria.length) return false;
  const names = fields.map((field) => field.fieldname);
  if (new Set(names).size !== names.length) return false;

  const unified = fields.filter((field) => field.source === "Unified Person");
  if (unified.length) return unified.length === 1 && fields.length === 1;
  if (fields.some((field) => field.strong_identifier)) return true;
  if (
    fields.some((field) => field.data_kind === "Name")
    && fields.some((field) => field.data_kind === "Date")
  ) return true;
  return fields.every((field) => field.data_kind === "Name")
    && fields.some((field) => FIRST_NAME_FIELDS.has(field.fieldname));
}

export function searchGuidance(criteria, searchFields) {
  const fields = selectedFields(criteria, searchFields);
  if (!fields.length || isAllowedSearchShape(criteria, searchFields)) return null;

  const names = fields.map((field) => field.fieldname);
  if (new Set(names).size !== names.length) return { code: "duplicate" };
  if (fields.some((field) => field.source === "Unified Person")) return { code: "unified-alone" };

  // A user may add the next empty row before deciding which companion field
  // to use. Do not describe an otherwise valid standalone selection as
  // invalid merely because that new row is still blank.
  if (fields.length < criteria.length) {
    const selectedCriteria = criteria.filter((row) => names.includes(row.fieldname));
    if (isAllowedSearchShape(selectedCriteria, searchFields)) return null;
  }

  if (fields.length === 1) {
    const field = fields[0];
    if (field.data_kind === "Date") return { code: "date-needs-name", label: field.label };
    if (field.fieldname === "eng_surname") return { code: "english-surname-needs-companion", label: field.label };
    if (field.fieldname === "chi_surname") return { code: "chinese-surname-needs-companion", label: field.label };
    return { code: "field-needs-companion", label: field.label };
  }
  return { code: "unsupported-combination" };
}

export function shouldAppendSearchCriterion(criteria, searchFields, maximumCriteria = 5) {
  if (criteria.length >= maximumCriteria || criteria.some((row) => !row.fieldname)) return false;
  const guidance = searchGuidance(criteria, searchFields);
  return Boolean(guidance && GUIDANCE_FIXED_BY_ANOTHER_CRITERION.has(guidance.code));
}
