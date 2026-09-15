from nimbledesk.perception.ocr import _matches


def test_tesseract_words_are_grouped_into_phrase_matches() -> None:
    header = "\t".join(
        (
            "level", "page_num", "block_num", "par_num", "line_num", "word_num",
            "left", "top", "width", "height", "conf", "text",
        )
    )
    tsv = "\n".join(
        (
            header,
            "5\t1\t1\t1\t1\t1\t10\t20\t50\t20\t94\tCreate",
            "5\t1\t1\t1\t1\t2\t65\t20\t60\t20\t92\tproject",
            "5\t1\t1\t1\t2\t1\t10\t60\t50\t20\t70\tCancel",
        )
    )

    matches = _matches(tsv, "create project", exact=True)

    assert len(matches) == 1
    assert matches[0].text == "Create project"
    assert matches[0].confidence == 0.92
    assert matches[0].bounds.model_dump() == {
        "left": 10,
        "top": 20,
        "width": 115,
        "height": 20,
    }
