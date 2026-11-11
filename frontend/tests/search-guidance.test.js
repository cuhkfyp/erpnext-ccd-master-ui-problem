import assert from "node:assert/strict";
import test from "node:test";

import {
  isAllowedSearchShape,
  searchFieldRequiresCompanion,
  searchGuidance,
  shouldAppendSearchCriterion,
} from "../src/searchGuidance.js";

const fields = [
  { source: "CCD Master", fieldname: "hksr_num", label: "Membership Number", data_kind: "Identifier", strong_identifier: true },
  { source: "CCD Master", fieldname: "eng_firstname", label: "English First Name", data_kind: "Name", strong_identifier: false },
  { source: "CCD Master", fieldname: "eng_surname", label: "English Surname", data_kind: "Name", strong_identifier: false },
  { source: "CCD Master", fieldname: "chi_firstname", label: "Chinese First Name", data_kind: "Name", strong_identifier: false },
  { source: "CCD Master", fieldname: "chi_surname", label: "Chinese Surname", data_kind: "Name", strong_identifier: false },
  { source: "CCD Master", fieldname: "birthday", label: "Date of Birth", data_kind: "Date", strong_identifier: false },
  { source: "Unified Person", fieldname: "unified_person_number", label: "Unified Person Number", data_kind: "Identifier", strong_identifier: true },
];

const criteria = (...fieldnames) => fieldnames.map((fieldname) => ({ fieldname, value: "value" }));

test("approved identifiers and exact first names may be searched alone", () => {
  assert.equal(isAllowedSearchShape(criteria("hksr_num"), fields), true);
  assert.equal(isAllowedSearchShape(criteria("eng_firstname"), fields), true);
  assert.equal(isAllowedSearchShape(criteria("chi_firstname"), fields), true);
  assert.equal(searchFieldRequiresCompanion(fields[1]), false);
});

test("an added blank row does not invalidate a standalone first name", () => {
  const blankAfterChineseFirstName = [
    { fieldname: "chi_firstname", value: "根深" },
    { fieldname: "", value: "" },
  ];
  const blankAfterIdentifier = [
    { fieldname: "hksr_num", value: "12345" },
    { fieldname: "", value: "" },
  ];
  assert.equal(searchGuidance(blankAfterChineseFirstName, fields), null);
  assert.equal(searchGuidance(blankAfterIdentifier, fields), null);
});

test("surname and date selections immediately request a companion", () => {
  assert.equal(searchFieldRequiresCompanion(fields[2]), true);
  assert.equal(searchFieldRequiresCompanion(fields[5]), true);
  assert.equal(searchGuidance(criteria("eng_surname"), fields).code, "english-surname-needs-companion");
  assert.equal(searchGuidance(criteria("birthday"), fields).code, "date-needs-name");
  assert.equal(
    searchGuidance([{ fieldname: "eng_surname", value: "Smith" }, { fieldname: "", value: "" }], fields).code,
    "english-surname-needs-companion",
  );
});

test("valid name combinations clear the guidance", () => {
  assert.equal(isAllowedSearchShape(criteria("eng_firstname", "eng_surname"), fields), true);
  assert.equal(isAllowedSearchShape(criteria("chi_surname", "chi_firstname"), fields), true);
  assert.equal(isAllowedSearchShape(criteria("eng_surname", "birthday"), fields), true);
  assert.equal(searchGuidance(criteria("eng_firstname", "eng_surname"), fields), null);
  assert.equal(
    isAllowedSearchShape(criteria("chi_surname", "eng_firstname", "eng_surname"), fields),
    true,
  );
  assert.equal(
    searchGuidance(criteria("chi_surname", "eng_firstname", "eng_surname"), fields),
    null,
  );
  assert.equal(
    shouldAppendSearchCriterion(criteria("chi_surname", "eng_firstname", "eng_surname"), fields),
    false,
  );
});

test("each still-incomplete selection requests another criterion", () => {
  assert.equal(shouldAppendSearchCriterion(criteria("eng_surname"), fields), true);
  assert.equal(shouldAppendSearchCriterion(criteria("eng_surname", "chi_surname"), fields), true);
  assert.equal(
    shouldAppendSearchCriterion(criteria("eng_surname", "chi_surname", "eng_surname"), fields),
    false,
  );
  assert.equal(
    shouldAppendSearchCriterion(
      [{ fieldname: "eng_surname", value: "Smith" }, { fieldname: "", value: "" }],
      fields,
    ),
    false,
  );
});

test("auto-add stops once a valid companion is selected", () => {
  assert.equal(shouldAppendSearchCriterion(criteria("eng_surname", "birthday"), fields), false);
  assert.equal(shouldAppendSearchCriterion(criteria("chi_surname", "hksr_num"), fields), false);
  assert.equal(shouldAppendSearchCriterion(criteria("eng_firstname"), fields), false);
  assert.equal(shouldAppendSearchCriterion(criteria("chi_firstname"), fields), false);
});

test("unified person number still has to remain standalone", () => {
  assert.equal(isAllowedSearchShape(criteria("unified_person_number"), fields), true);
  assert.equal(isAllowedSearchShape(criteria("unified_person_number", "birthday"), fields), false);
  assert.equal(searchGuidance(criteria("unified_person_number", "birthday"), fields).code, "unified-alone");
  assert.equal(shouldAppendSearchCriterion(criteria("unified_person_number", "birthday"), fields), false);
});
