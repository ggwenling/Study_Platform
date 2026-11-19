import json

from study_platform.services.studio import _parse_flashcards_json


def test_flashcard_source_indices_are_validated_before_deduplication():
    response = json.dumps({
        "cards": [
            {"front": "Skipped", "back": "No valid source", "source_indices": [{"index": 2}]},
            {
                "front": "Kept", "back": "Two sources",
                "source_indices": [{}, [2], True, 3, 2, 3, None, 4, "1", 1.0],
            },
            {"front": "Also kept", "back": "One source", "source_indices": [1]},
        ]
    })
    assert _parse_flashcards_json(response, 3) == [
        ("Kept", "Two sources", [3, 2]),
        ("Also kept", "One source", [1]),
    ]
