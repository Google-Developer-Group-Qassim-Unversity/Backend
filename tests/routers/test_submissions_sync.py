# Real captured response payload (form 156, prod) - the email lives under
# questionId "128490c7" here, but under a completely different ID on other
# forms, and that same ID ("196b7896") holds a multiple-choice answer on
# this form instead of an email. Question IDs and titles are not stable
# across forms, so extract_email_answer must find the email by shape, not
# by a fixed ID or title.
REAL_FORM_156_RESPONSE_ANSWERS = {
    "196b7896": {"questionId": "196b7896", "textAnswers": {"answers": [{"value": "Option 1"}]}},
    "32cf98a8": {"questionId": "32cf98a8", "textAnswers": {"answers": [{"value": "طلاب"}]}},
    "2d0d7dad": {"questionId": "2d0d7dad", "textAnswers": {"answers": [{"value": "asdf"}]}},
    "36c5263a": {"questionId": "36c5263a", "textAnswers": {"answers": [{"value": "Option 1"}]}},
    "3b0efcf2": {"questionId": "3b0efcf2", "textAnswers": {"answers": [{"value": "Abdlelah Albrrak"}]}},
    "128490c7": {"questionId": "128490c7", "textAnswers": {"answers": [{"value": "albrrak337@gmail.com"}]}},
}


def test_extract_email_answer_finds_email_regardless_of_question_id():
    from app.routers.submissions import extract_email_answer

    assert extract_email_answer(REAL_FORM_156_RESPONSE_ANSWERS) == "albrrak337@gmail.com"


def test_extract_email_answer_normalizes_case():
    from app.routers.submissions import extract_email_answer

    answers = {"q1": {"textAnswers": {"answers": [{"value": "Someone@Example.COM"}]}}}
    assert extract_email_answer(answers) == "someone@example.com"


def test_extract_email_answer_returns_none_when_no_email_shaped_value():
    from app.routers.submissions import extract_email_answer

    answers = {
        "q1": {"textAnswers": {"answers": [{"value": "Option 1"}]}},
        "q2": {"textAnswers": {"answers": [{"value": "Abdlelah Albrrak"}]}},
    }
    assert extract_email_answer(answers) is None


def test_extract_email_answer_handles_empty_answers():
    from app.routers.submissions import extract_email_answer

    assert extract_email_answer({}) is None
