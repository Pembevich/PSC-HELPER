import csv
import json

INPUT_FILE = "lead.csv"
OUTPUT_FILE = "light.json"


def normalize_row(row: dict[str, str]) -> tuple[str, int]:
    """Возвращает guidance и distance из строки независимо от варианта заголовков."""
    guidance = (row.get("guidance") or row.get("куда править") or "").strip()
    distance_raw = (row.get("distance") or row.get("расстояние") or "0").strip()
    distance = int(distance_raw)
    return guidance, distance


def classify_places() -> None:
    result = {
        "probable": [],
        "reliable": [],
        "rest": [],
    }

    with open(INPUT_FILE, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, delimiter=".")
        for row in reader:
            guidance, distance = normalize_row(row)
            place = (row.get("place") or row.get("предполагаемое место") or "").strip()

            same_mod = len(guidance) % 5 == distance % 5
            odd_words = len(guidance.split()) % 2 == 1

            if same_mod and odd_words:
                result["reliable"].append(place)
            elif same_mod != odd_words:
                result["probable"].append(place)
            else:
                result["rest"].append(place)

    for key in result:
        result[key].sort()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    classify_places()
