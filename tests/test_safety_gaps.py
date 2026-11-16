"""Safety gaps found by independent reviews, on fictional text.

B1 a question is not a statement; a patient report needs the patient's words.
B2 a vital-sign unit must be spoken (or equal the profile's single fixed unit).
B3 a number must belong to its own measurement or medicine.
B4 disfluency matching never collapses numbers and never treats "ER" as a filler.
Each case pairs the unsafe input with the nearby safe one that must still pass.
"""

import pytest

from auris.assertions import EvidenceItem
from auris.consistency import number_attached
from auris.evidence import EvidenceFailure, align_evidence_item
from auris.transcript import Transcript


def transcript(*segments):
    return {
        "schema_version": "1",
        "segments": [
            {
                "id": segment_id,
                "speaker_cluster_id": f"fictional-{role}",
                "role": role,
                "start_seconds": float(index * 5),
                "end_seconds": float(index * 5 + 4),
                "text": text,
                "words": None,
            }
            for index, (segment_id, role, text) in enumerate(segments)
        ],
    }


def evidence(segment_id, quote):
    return {"evidence": [{"segment_id": segment_id, "quote": quote}]}


# --- B1: questions and the patient's own words ------------------------------------------


def test_condition_from_a_question_answered_no_goes_to_review(map_case):
    _, _, outcome = map_case(
        "patient_reported_condition",
        changes={
            "concept_text": "asthma",
            "source_speaker_role": "clinician",
            **evidence("s1", "Do you have asthma?"),
        },
        transcript=transcript(("s1", "clinician", "Do you have asthma?"), ("s2", "patient", "No.")),
    )
    for reason in (
        "quote_is_question",
        "answer_denies_question",
        "patient_report_without_patient_turn",
    ):
        assert reason in outcome.review_reasons
    assert outcome.export_state != "final_exportable"


@pytest.mark.parametrize(
    ("reply", "denies"),
    [
        ("Yes, since I was a child.", False),
        ("I hope not.", False),
        ("Um, no, never.", True),
        ("I don't, no.", True),
        ("Nope.", True),
    ],
)
def test_only_a_denying_reply_counts_as_answering_no(map_case, reply, denies):
    _, _, outcome = map_case(
        "symptom-affirmed",
        changes={"concept_text": "cough", **evidence("s1", "Do you have a cough?")},
        transcript=transcript(
            ("s1", "clinician", "Do you have a cough?"), ("s2", "patient", reply)
        ),
    )
    assert "quote_is_question" in outcome.review_reasons
    assert ("answer_denies_question" in outcome.review_reasons) is denies


def test_clinician_question_is_not_an_assessment(map_case):
    _, _, outcome = map_case(
        "clinician_assessed_problem",
        changes={"concept_text": "pneumonia", **evidence("s1", "Could this be pneumonia?")},
        transcript=transcript(
            ("s1", "clinician", "Could this be pneumonia?"), ("s2", "patient", "I hope not.")
        ),
    )
    assert "quote_is_question" in outcome.review_reasons
    assert "answer_denies_question" not in outcome.review_reasons


def test_unpunctuated_speech_to_text_question_is_detected(map_case):
    _, _, outcome = map_case(
        "symptom-affirmed",
        changes={"concept_text": "chest pain", **evidence("s1", "do you have any chest pain")},
        transcript=transcript(
            ("s1", "clinician", "do you have any chest pain"), ("s2", "patient", "no")
        ),
    )
    assert {"quote_is_question", "answer_denies_question"} <= set(outcome.review_reasons)


def test_statement_beside_a_question_is_still_evidence(map_case):
    _, _, outcome = map_case(
        "symptom-affirmed",
        changes={"concept_text": "cough", **evidence("s2", "I have had a cough for a week.")},
        transcript=transcript(
            ("s1", "clinician", "Do you have a cough?"),
            ("s2", "patient", "I have had a cough for a week."),
        ),
    )
    assert "quote_is_question" not in outcome.review_reasons


def test_statements_followed_by_a_question_are_not_a_question(map_case):
    text = "Itchy. OK. So your bowels are working OK?"
    _, _, outcome = map_case(
        "symptom-affirmed",
        changes={"concept_text": "itchy", **evidence("s1", text)},
        transcript=transcript(("s1", "clinician", text), ("s2", "patient", "No.")),
    )
    assert "quote_is_question" not in outcome.review_reasons
    assert "answer_denies_question" not in outcome.review_reasons


def test_patient_report_quoting_only_the_clinician_goes_to_review(map_case):
    _, _, outcome = map_case(
        "patient_reported_condition",
        changes={
            "concept_text": "asthma",
            "source_speaker_role": "clinician",
            **evidence("s1", "You mentioned you have asthma."),
        },
        transcript=transcript(("s1", "clinician", "You mentioned you have asthma.")),
    )
    assert "patient_report_without_patient_turn" in outcome.review_reasons
    assert "quote_is_question" not in outcome.review_reasons


# --- B2: a unit must be spoken ----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "quote", "value", "unit"),
    [
        ("body-weight", "Your weight is 70.", "70", "kg"),
        ("body-temperature", "Your temperature is 38.5.", "38.5", "Cel"),
        ("body-height", "You are 170 tall.", "170", "cm"),
    ],
)
def test_unspoken_unit_never_becomes_a_record(map_case, name, quote, value, unit):
    _, _, outcome = map_case(name, changes={"value_text": value, "unit_text": unit}, quote=quote)
    assert outcome.resource is None
    assert "unit_not_in_quote" in outcome.review_reasons


@pytest.mark.parametrize(
    ("quote", "unit"),
    [("Your weight is 70 kilograms.", "kg"), ("Your weight is 70 kg.", "kilograms")],
)
def test_spoken_unit_in_any_spelling_is_accepted(map_case, quote, unit):
    _, _, outcome = map_case(
        "body-weight", changes={"value_text": "70", "unit_text": unit}, quote=quote
    )
    assert "unit_not_in_quote" not in outcome.review_reasons
    assert outcome.resource["valueQuantity"]["code"] == "kg"


def test_unspoken_unit_equal_to_the_fixed_profile_unit_is_noted_not_refused(map_case):
    _, _, outcome = map_case(
        "heart-rate",
        changes={"value_text": "84", "unit_text": "bpm"},
        quote="Your heart rate is 84.",
    )
    assert outcome.resource["valueQuantity"]["code"] == "/min"
    assert "unit_not_in_quote" not in outcome.review_reasons
    assert any("unit not spoken" in note for note in outcome.provenance_notes)


def test_unspoken_unit_that_contradicts_the_fixed_unit_is_refused(map_case):
    _, _, outcome = map_case(
        "blood-pressure",
        changes={"value_text": "128 over 82", "unit_text": "kPa"},
        quote="Your blood pressure is 128 over 82.",
    )
    assert outcome.resource is None
    assert {"unit_not_in_quote", "unit_not_in_profile_set"} <= set(outcome.review_reasons)


# --- B3: a number must belong to its own measurement ------------------------------------

READINGS = "Pulse is 72 and blood pressure 128 over 82."


@pytest.mark.parametrize(
    ("aliases", "value", "attached"),
    [
        (["heart rate", "pulse"], "128", False),
        (["heart rate", "pulse"], "72", True),
        (["blood pressure", "bp"], "128 over 82", True),
        (["blood pressure", "bp"], "72 over 82", False),
        (["respiratory rate"], "72", None),  # concept not mentioned: unknown
    ],
)
def test_number_attachment(aliases, value, attached):
    assert number_attached(READINGS, aliases, value) is attached


def test_number_alone_in_its_quote_is_attached():
    assert number_attached("Your heart rate is 84.", ["heart rate"], "84") is True
    assert number_attached("84 is your pulse.", ["pulse"], "84") is True


def test_borrowed_vital_number_goes_to_review(map_case):
    _, _, outcome = map_case(
        "heart-rate", changes={"value_text": "128", "unit_text": None}, quote=READINGS
    )
    assert "number_not_attached_to_concept" in outcome.review_reasons
    _, _, outcome = map_case(
        "heart-rate", changes={"value_text": "72", "unit_text": None}, quote=READINGS
    )
    assert "number_not_attached_to_concept" not in outcome.review_reasons


@pytest.mark.parametrize(("dose", "flagged"), [("400 milligrams", True), ("500 milligrams", False)])
def test_borrowed_dose_goes_to_review(map_case, dose, flagged):
    quote = (
        "I will prescribe ibuprofen 400 milligrams and amoxicillin 500 milligrams by mouth "
        "three times a day."
    )
    _, _, outcome = map_case("new-order", changes={"dose_text": dose}, quote=quote)
    assert ("number_not_attached_to_concept" in outcome.review_reasons) is flagged


# --- B4: numbers and "ER" survive disfluency matching -----------------------------------


def align(segment_text, quote):
    text = Transcript.model_validate(transcript(("s1", "patient", segment_text)))
    return align_evidence_item(EvidenceItem(segment_id="s1", quote=quote), text)


@pytest.mark.parametrize(
    ("segment_text", "quote"),
    [
        ("Take 2 2 mg at night.", "Take 2 mg at night."),
        ("It was 1 1 0 over 70.", "It was 1 0 over 70."),
        ("I went to the ER yesterday.", "I went to the yesterday."),
    ],
)
def test_numbers_and_er_are_never_dropped(segment_text, quote):
    assert align(segment_text, quote).failure == EvidenceFailure.QUOTE_NOT_FOUND


@pytest.mark.parametrize(
    ("segment_text", "quote"),
    [
        ("I er went to the clinic.", "I went to the clinic."),
        ("It's on the, on the hands.", "It's on the hands."),
        ("I take um it at night.", "I take it at night."),
    ],
)
def test_ordinary_disfluencies_still_match(segment_text, quote):
    result = align(segment_text, quote)
    assert result.failure is None
    assert result.aligned.alignment_method == "disfluency-v2"


# --- Second, adversarial review: bypasses it proved, now closed ----------------------


@pytest.mark.parametrize(
    ("name", "value", "unit", "segments", "cited"),
    [
        # The unit is said, but for a different number.
        (
            "body-weight",
            "154",
            "kg",
            [("s1", "clinician", "Your weight is 154 pounds, about 70 kilograms.")],
            ["s1"],
        ),
        (
            "body-temperature",
            "101",
            "Cel",
            [("s1", "clinician", "Your temperature is 101 Fahrenheit, about 38.3 Celsius.")],
            ["s1"],
        ),
        # The unit is only in another quote.
        (
            "body-weight",
            "154",
            "kg",
            [
                ("s1", "clinician", "Your weight is 154."),
                ("s2", "patient", "I was 75 kilograms last year."),
            ],
            ["s1", "s2"],
        ),
    ],
)
def test_unit_must_follow_its_own_value(map_case, name, value, unit, segments, cited):
    texts = {sid: text for sid, _, text in segments}
    _, _, outcome = map_case(
        name,
        changes={
            "value_text": value,
            "unit_text": unit,
            "evidence": [{"segment_id": sid, "quote": texts[sid]} for sid in cited],
        },
        transcript=transcript(*segments),
    )
    assert outcome.resource is None
    assert "unit_not_in_quote" in outcome.review_reasons


@pytest.mark.parametrize(
    ("name", "quote", "value", "unit", "code"),
    [
        ("body-weight", "Your weight is 70kg.", "70", "kg", "kg"),
        ("body-weight", "Your weight is 70 kilos.", "70", "kg", "kg"),
        ("body-weight", "Your weight is 70 kilogrammes.", "70", "kg", "kg"),
        ("body-height", "Your height is 172cm.", "172", "cm", "cm"),
        ("body-temperature", "Your temperature is 38.5C.", "38.5", "Cel", "Cel"),
    ],
)
def test_compact_and_british_unit_spellings_are_accepted(map_case, name, quote, value, unit, code):
    _, _, outcome = map_case(name, changes={"value_text": value, "unit_text": unit}, quote=quote)
    assert "unit_not_in_quote" not in outcome.review_reasons
    assert outcome.resource["valueQuantity"]["code"] == code


def test_unit_the_model_left_out_is_taken_from_the_words_after_the_value(map_case):
    _, _, outcome = map_case(
        "body-weight",
        changes={"value_text": "70", "unit_text": None},
        quote="Your weight is 70 kilograms.",
    )
    assert outcome.resource["valueQuantity"]["code"] == "kg"
    assert any("directly after the value" in note for note in outcome.provenance_notes)


def test_a_bare_degree_never_picks_a_temperature_scale(map_case):
    _, _, outcome = map_case(
        "body-temperature",
        changes={"value_text": "38", "unit_text": "Cel"},
        quote="Your temperature is 38 degrees.",
    )
    assert outcome.resource is None


VITALS = ["pulse ox", "blood pressure", "respiratory rate", "pulse", "heart rate", "temperature"]


@pytest.mark.parametrize(
    ("quote", "aliases", "value", "attached"),
    [
        (
            "Your pulse is normal and your blood pressure is 128 over 82.",
            ["heart rate", "pulse"],
            "128",
            False,
        ),
        ("Pulse 72, pulse ox 98.", ["heart rate", "pulse"], "98", False),
        ("Pulse 72, pulse ox 98.", ["heart rate", "pulse"], "72", True),
        (
            "Your pulse is seventy two and blood pressure 128 over 82.",
            ["heart rate", "pulse"],
            "128",
            False,
        ),
        ("Your respiratory rate is normal, pulse 88.", ["respiratory rate"], "88", False),
        (
            "128 over 82 is your blood pressure and your pulse is 72.",
            ["blood pressure"],
            "128 over 82",
            True,
        ),
        ("128 over 82 is your blood pressure and your pulse is 72.", ["pulse"], "72", True),
        ("Your pulse at 9:15 was 72.", ["pulse"], "72", True),
        ("Your pulse is fine.", ["pulse"], "128", None),
    ],
)
def test_number_attachment_second_review(quote, aliases, value, attached):
    from auris.consistency import MEASUREMENT_NAMES

    assert number_attached(quote, aliases, value, MEASUREMENT_NAMES) is attached


def test_concept_and_value_split_across_quotes_go_to_review(map_case):
    _, _, outcome = map_case(
        "heart-rate",
        changes={
            "value_text": "128",
            "unit_text": None,
            "evidence": [
                {"segment_id": "s1", "quote": "Your pulse is fine."},
                {"segment_id": "s2", "quote": "Blood pressure 128 over 82."},
            ],
        },
        transcript=transcript(
            ("s1", "clinician", "Your pulse is fine."),
            ("s2", "clinician", "Blood pressure 128 over 82."),
        ),
    )
    assert "number_not_attached_to_concept" in outcome.review_reasons


@pytest.mark.parametrize(
    ("question", "answer", "value"),
    [
        ("Is your temperature 38.5 degrees Celsius now?", "No, it's 37.", "38.5"),
        ("Is your pulse still 110.", "No, it's 80.", "110"),
        ("Right. and is your pulse still 110", "No, it's 80.", "110"),
        ("Was your pulse 110 on Dr. Shah's machine?", "No, it's 80.", "110"),
    ],
)
def test_vital_from_a_question_goes_to_review(map_case, question, answer, value):
    name = "body-temperature" if "temperature" in question else "heart-rate"
    _, _, outcome = map_case(
        name,
        changes={
            "value_text": value,
            "unit_text": "degrees Celsius" if name == "body-temperature" else None,
            **evidence("s1", question),
        },
        transcript=transcript(("s1", "clinician", question), ("s2", "patient", answer)),
    )
    assert {"quote_is_question", "answer_denies_question"} <= set(outcome.review_reasons)


def test_a_question_plus_an_unrelated_statement_is_still_a_question(map_case):
    _, _, outcome = map_case(
        "heart-rate",
        changes={
            "value_text": "110",
            "unit_text": None,
            "evidence": [
                {"segment_id": "s1", "quote": "Is your pulse still 110?"},
                {"segment_id": "s2", "quote": "Okay."},
            ],
        },
        transcript=transcript(
            ("s1", "clinician", "Is your pulse still 110?"), ("s2", "patient", "Okay.")
        ),
    )
    assert "quote_is_question" in outcome.review_reasons


def test_question_with_an_answer_carrying_the_fact_is_supported(map_case):
    _, _, outcome = map_case(
        "symptom-affirmed",
        changes={
            "concept_text": "cough",
            "evidence": [
                {"segment_id": "s1", "quote": "Do you have a cough?"},
                {"segment_id": "s2", "quote": "Yes, a dry cough for a week."},
            ],
        },
        transcript=transcript(
            ("s1", "clinician", "Do you have a cough?"),
            ("s2", "patient", "Yes, a dry cough for a week."),
        ),
    )
    assert "quote_is_question" not in outcome.review_reasons


def test_denied_checklist_question_supports_a_negated_fact(map_case):
    question = "No cough, no breathing difficulties?"
    _, _, outcome = map_case(
        "symptom-negated",
        changes={"concept_text": "cough", **evidence("s1", question)},
        transcript=transcript(("s1", "clinician", question), ("s2", "patient", "No.")),
    )
    assert "quote_is_question" not in outcome.review_reasons


@pytest.mark.parametrize(
    ("text", "question"),
    [
        ("Could it be acute bronchitis.", True),  # a patient's question without "?"
        ("had it since Monday", False),  # a patient's subject-dropped statement
        ("is the worst at night", False),
    ],
)
def test_patient_question_opener_is_narrow(map_case, text, question):
    _, _, outcome = map_case(
        "symptom-affirmed",
        changes={"concept_text": "cough", "source_speaker_role": "patient", **evidence("s1", text)},
        transcript=transcript(("s1", "patient", text), ("s2", "clinician", "No, it isn't.")),
    )
    assert ("quote_is_question" in outcome.review_reasons) is question


@pytest.mark.parametrize(
    ("reply", "flagged"),
    [("Maybe.", True), ("Okay.", True), ("Yeah.", False), ("I do.", False), ("I do not.", True)],
)
def test_patient_report_needs_the_patients_words_or_a_yes(map_case, reply, flagged):
    _, _, outcome = map_case(
        "patient_reported_condition",
        changes={
            "concept_text": "asthma",
            "source_speaker_role": "clinician",
            "evidence": [
                {"segment_id": "s1", "quote": "You mentioned you have asthma."},
                {"segment_id": "s2", "quote": reply},
            ],
        },
        transcript=transcript(
            ("s1", "clinician", "You mentioned you have asthma."), ("s2", "patient", reply)
        ),
    )
    assert ("patient_report_without_patient_turn" in outcome.review_reasons) is flagged


@pytest.mark.parametrize(
    ("reply", "denial"),
    [
        ("None.", True),
        ("Asthma? No.", True),
        ("Hmm, no.", True),
        ("Oh no, never.", True),
        ("I do not.", True),
        ("I don't think so.", True),
        ("I don't know.", False),
        ("I didn't catch that.", False),
        ("No idea.", False),
        ("I'm not sure.", False),
        ("I hope not.", False),
    ],
)
def test_reply_denial_wording(reply, denial):
    from auris.consistency import _is_denial, _opening_words

    assert _is_denial(_opening_words(reply)) is denial


@pytest.mark.parametrize(
    ("segment_text", "quote"),
    [
        ("It was one one zero over seventy.", "It was one zero over seventy."),
        ("I take ten ten milligrams.", "I take ten milligrams."),
        ("Take ½ ½ tablet.", "Take ½ tablet."),
        ("I went to the Er department.", "I went to the department."),
    ],
)
def test_number_words_fractions_and_capitalised_er_are_never_dropped(segment_text, quote):
    assert align(segment_text, quote).failure == EvidenceFailure.QUOTE_NOT_FOUND


# --- Third pass: bypasses proved by the second review, now closed --------------------


@pytest.mark.parametrize(
    ("question", "reply"),
    [
        ("Thanks. Is your pulse still 110?", "No, it's 80 now."),
        ("Okay, thank you. Is your pulse still 110?", "No, never."),
        ("Is your pulse still 110? That would explain the dizziness.", "No."),
    ],
)
def test_a_question_beside_other_sentences_is_still_a_question(map_case, question, reply):
    _, _, outcome = map_case(
        "heart-rate",
        changes={"value_text": "110", "unit_text": None, **evidence("s1", question)},
        transcript=transcript(("s1", "clinician", question), ("s2", "patient", reply)),
    )
    assert {"quote_is_question", "answer_denies_question"} <= set(outcome.review_reasons)


def test_unpunctuated_question_mid_sentence(map_case):
    text = "so I see you have an inhaler do you get heartburn"
    _, _, outcome = map_case(
        "symptom-affirmed",
        changes={"concept_text": "heartburn", **evidence("s1", text)},
        transcript=transcript(("s1", "clinician", text), ("s2", "patient", "no never")),
    )
    assert "quote_is_question" in outcome.review_reasons


def test_a_denied_question_flags_an_affirmed_fact_even_with_a_supporting_sentence(map_case):
    statement, question = "Heartburn can cause this sort of chest pain.", "Do you get heartburn?"
    _, _, outcome = map_case(
        "symptom-affirmed",
        changes={
            "concept_text": "heartburn",
            "evidence": [
                {"segment_id": "s1", "quote": statement},
                {"segment_id": "s2", "quote": question},
            ],
        },
        transcript=transcript(
            ("s1", "clinician", statement),
            ("s2", "clinician", question),
            ("s3", "patient", "No, never."),
        ),
    )
    assert "answer_denies_question" in outcome.review_reasons


def test_answer_is_the_turn_after_the_question_not_a_backchannel(map_case):
    data = transcript(
        ("s1", "clinician", "Do you get heartburn?"),
        ("s2", "patient", "Uh, no."),
        ("s3", "patient", "Yes, quite often actually."),
    )
    data["segments"][0].update(start_seconds=0.0, end_seconds=5.0)
    data["segments"][1].update(start_seconds=2.0, end_seconds=2.6)  # overlaps the question
    data["segments"][2].update(start_seconds=5.4, end_seconds=8.0)
    _, _, outcome = map_case(
        "symptom-affirmed",
        changes={"concept_text": "heartburn", **evidence("s1", "Do you get heartburn?")},
        transcript=data,
    )
    assert "quote_is_question" in outcome.review_reasons
    assert "answer_denies_question" not in outcome.review_reasons


@pytest.mark.parametrize(
    ("quote", "aliases", "value", "attached"),
    [
        ("Your blood pressure is 128 over 82 and your pulse is normal.", ["pulse"], "82", False),
        (
            "Your blood pressure is 128 over 82 and your pulse is normal.",
            ["blood pressure"],
            "128 over 82",
            True,
        ),
        ("Temperature 37.2, pulse fine.", ["pulse"], "37.2", False),
        ("Heart rate is 97, oxygen saturation normal.", ["oxygen saturation"], "97", False),
        (
            "So 98 percent for the oxygen saturation, and 72 for the pulse.",
            ["oxygen saturation"],
            "72",
            False,
        ),
        (
            "So 98 percent for the oxygen saturation, and 72 for the pulse.",
            ["oxygen saturation"],
            "98",
            True,
        ),
        ("So 98 percent for the oxygen saturation, and 72 for the pulse.", ["pulse"], "72", True),
        ("Pulse is fine, BP's 140 over 90.", ["pulse"], "140", False),
        ("Your pressure is 128 over 82.", ["pulse"], "128", None),  # never named: flagged
        ("Resps 16, pulse fine.", ["pulse"], "16", False),
        ("Your pulse rate is 72.", ["heart rate", "pulse"], "72", True),
        ("Your pulse at 9.15 was 72.", ["pulse"], "72", True),
        ("Your pulse at 9am was 72.", ["pulse"], "72", True),
    ],
)
def test_number_attachment_third_pass(quote, aliases, value, attached):
    from auris.consistency import measurement_stops

    assert number_attached(quote, aliases, value, measurement_stops(aliases)) is attached


@pytest.mark.parametrize(
    ("name", "quote", "value", "unit", "code"),
    [
        ("body-weight", "Your weight is 70 KG.", "70", "kg", "kg"),
        ("body-weight", "Your weight is 70Kg.", "70", "kg", "kg"),
        ("body-height", "Your height is 172 CM.", "172", "cm", "cm"),
        ("body-temperature", "Your temperature is 38.5 degrees C.", "38.5", "Cel", "Cel"),
        ("body-temperature", "Your temperature is 38.5℃.", "38.5", "Cel", "Cel"),
    ],
)
def test_unit_spellings_third_pass(map_case, name, quote, value, unit, code):
    _, _, outcome = map_case(name, changes={"value_text": value, "unit_text": unit}, quote=quote)
    assert outcome.resource["valueQuantity"]["code"] == code


@pytest.mark.parametrize("reply", ["Mm-hmm.", "Mhm.", "Uh-huh."])
def test_assent_sounds_count_as_the_patients_yes(map_case, reply):
    _, _, outcome = map_case(
        "patient_reported_condition",
        changes={
            "concept_text": "asthma",
            "source_speaker_role": "clinician",
            "evidence": [
                {"segment_id": "s1", "quote": "You mentioned you have asthma."},
                {"segment_id": "s2", "quote": reply},
            ],
        },
        transcript=transcript(
            ("s1", "clinician", "You mentioned you have asthma."), ("s2", "patient", reply)
        ),
    )
    assert "patient_report_without_patient_turn" not in outcome.review_reasons


@pytest.mark.parametrize("placeholder", ["not stated", "Not mentioned", "not specified"])
def test_placeholder_words_are_empty_fields_not_values(map_case, placeholder):
    # Seen in a real demo run: the model wrote "not stated" for the unit and date
    # of an in-visit blood pressure, which refused a correct reading.
    _, _, outcome = map_case(
        "blood-pressure",
        changes={"unit_text": placeholder, "stated_time_text": placeholder},
        quote="Your blood pressure is 128 over 82.",
    )
    assert outcome.resource is not None
    assert outcome.resource["component"][0]["valueQuantity"]["code"] == "mm[Hg]"
    assert "measurement_date_not_day_precision" not in outcome.review_reasons


@pytest.mark.parametrize("placeholder", ["not stated", "None", "n/a"])
def test_allergy_placeholders_are_not_a_resolution_or_a_reaction(map_case, placeholder):
    # Seen in a real desktop run: "not stated" in stated_resolution_text read as
    # the patient saying the allergy had resolved, and a placeholder reaction
    # would have been exported as the reaction's text.
    _, _, outcome = map_case(
        "allergy-ingredient",
        changes={"stated_resolution_text": placeholder, "reaction_manifestation_text": placeholder},
    )
    assert outcome.resource is not None
    assert "allergy_resolution_stated" not in outcome.review_reasons
    assert "reaction" not in outcome.resource
