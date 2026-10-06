"""Evaluation dataset and benchmark for the MedicalQueryRouter.

Tests routing accuracy across >=40 representative clinical queries spanning:
- STRUCTURED: Point queries, aggregations, vitals trends, temporal queries without narrative note requests
- RAG: Free-text summaries, medication lists, narrative clinical notes, doctor impressions, discharge overviews
- HYBRID: Correlating structured vitals/labs with narrative notes, recommendations, or treatment plans

Measures precision, recall, and overall accuracy, enforcing an accuracy threshold >= 90%.
"""
import pytest
from src.services.medical_query_router import MedicalQueryRoute, MedicalQueryRouter

# 45 representative clinical queries with ground-truth expected routes
ROUTING_EVAL_DATASET: list[dict[str, str]] = [
    # --- STRUCTURED (17 queries) ---
    {"query": "Latest BP?", "expected": "STRUCTURED"},
    {"query": "What was the blood pressure reading yesterday?", "expected": "STRUCTURED"},
    {"query": "What were the glucose readings in the last 3 days?", "expected": "STRUCTURED"},
    {"query": "Show pulse readings this week.", "expected": "STRUCTURED"},
    {"query": "What was yesterday's SpO2?", "expected": "STRUCTURED"},
    {"query": "What is the most recent heart rate recorded?", "expected": "STRUCTURED"},
    {"query": "Show body temperature trend over the past 7 days.", "expected": "STRUCTURED"},
    {"query": "List all fasting glucose values.", "expected": "STRUCTURED"},
    {"query": "What is the patient's latest HbA1c?", "expected": "STRUCTURED"},
    {"query": "Show respiratory rate measurements today.", "expected": "STRUCTURED"},
    {"query": "What was the patient's weight in the last 14 days?", "expected": "STRUCTURED"},
    {"query": "Blood pressure history over the past month.", "expected": "STRUCTURED"},
    {"query": "What was the recent hemoglobin level?", "expected": "STRUCTURED"},
    {"query": "Latest oxygen saturation level.", "expected": "STRUCTURED"},
    {"query": "Show recent FBS measurements.", "expected": "STRUCTURED"},
    {"query": "What was yesterday's body temp?", "expected": "STRUCTURED"},
    {"query": "Most recent diastolic and systolic pressure.", "expected": "STRUCTURED"},

    # --- RAG (16 queries) ---
    {"query": "Summarize the discharge summary.", "expected": "RAG"},
    {"query": "What did the radiologist conclude in the chest X-ray?", "expected": "RAG"},
    {"query": "What medications are listed in the admission note?", "expected": "RAG"},
    {"query": "What did the doctor say about chest pain?", "expected": "RAG"},
    {"query": "Give me an overview of the patient's past surgical history.", "expected": "RAG"},
    {"query": "What was the physician's diagnosis on admission?", "expected": "RAG"},
    {"query": "What were the patient's chief complaints?", "expected": "RAG"},
    {"query": "What follow-up instructions are documented?", "expected": "RAG"},
    {"query": "Summarize the echocardiogram findings.", "expected": "RAG"},
    {"query": "What allergies are noted in the clinical records?", "expected": "RAG"},
    {"query": "Explain the doctor's assessment and treatment plan.", "expected": "RAG"},
    {"query": "What did the cardiologist recommend during consultation?", "expected": "RAG"},
    {"query": "Was there any adverse drug reaction documented?", "expected": "RAG"},
    {"query": "What was the pathology report conclusion?", "expected": "RAG"},
    {"query": "What are the discharge instructions regarding diet?", "expected": "RAG"},
    {"query": "Who is the primary care physician mentioned?", "expected": "RAG"},

    # --- HYBRID (12 queries) ---
    {"query": "Compare latest BP with the discharge note recommendations.", "expected": "HYBRID"},
    {"query": "How does recent glucose compare with the doctor's plan?", "expected": "HYBRID"},
    {"query": "Correlate oxygen saturation with physical exam notes.", "expected": "HYBRID"},
    {"query": "What are the recent BP readings and what medications were prescribed for hypertension?", "expected": "HYBRID"},
    {"query": "How has the patient's blood pressure changed and what did the doctor say about it?", "expected": "HYBRID"},
    {"query": "What were the fever readings this week and what antibiotics were noted in the chart?", "expected": "HYBRID"},
    {"query": "Compare today's SpO2 level with the doctor's discharge instructions.", "expected": "HYBRID"},
    {"query": "What was the recent HbA1c and what dietary changes did the physician recommend?", "expected": "HYBRID"},
    {"query": "Show pulse rate readings and summarize what the cardiologist said about tachycardia.", "expected": "HYBRID"},
    {"query": "What was yesterday's weight and what treatment plan is noted for fluid overload?", "expected": "HYBRID"},
    {"query": "Latest hemoglobin level and any doctor notes regarding transfusion.", "expected": "HYBRID"},
    {"query": "Show fasting glucose trends and what the clinical note advises regarding insulin dosage.", "expected": "HYBRID"},
]


def test_medical_router_benchmark():
    router = MedicalQueryRouter()

    total = len(ROUTING_EVAL_DATASET)
    assert total >= 40, f"Benchmark dataset must have >=40 queries, got {total}"

    correct = 0
    confusion_matrix: dict[str, dict[str, int]] = {
        "STRUCTURED": {"STRUCTURED": 0, "RAG": 0, "HYBRID": 0},
        "RAG": {"STRUCTURED": 0, "RAG": 0, "HYBRID": 0},
        "HYBRID": {"STRUCTURED": 0, "RAG": 0, "HYBRID": 0},
    }
    mismatches = []

    for item in ROUTING_EVAL_DATASET:
        query_text = item["query"]
        expected_route = item["expected"]

        result = router.route_query(query_text)
        predicted_route = result.route.value

        confusion_matrix[expected_route][predicted_route] += 1

        if predicted_route == expected_route:
            correct += 1
        else:
            mismatches.append({
                "query": query_text,
                "expected": expected_route,
                "predicted": predicted_route,
            })

    accuracy = correct / total

    # Calculate per-class Precision and Recall
    metrics = {}
    for route_name in ["STRUCTURED", "RAG", "HYBRID"]:
        tp = confusion_matrix[route_name][route_name]
        fn = sum(confusion_matrix[route_name][p] for p in ["STRUCTURED", "RAG", "HYBRID"] if p != route_name)
        fp = sum(confusion_matrix[e][route_name] for e in ["STRUCTURED", "RAG", "HYBRID"] if e != route_name)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        metrics[route_name] = {"precision": precision, "recall": recall}

    print("\n--- Router Evaluation Benchmark Results ---")
    print(f"Total Evaluated: {total}")
    print(f"Correctly Classified: {correct}")
    print(f"Overall Accuracy: {accuracy * 100:.2f}%\n")
    for r_name, m in metrics.items():
        print(f"  {r_name}: Precision={m['precision'] * 100:.1f}%, Recall={m['recall'] * 100:.1f}%")

    if mismatches:
        print("\nMismatches:")
        for m in mismatches:
            print(f"  Query: '{m['query']}' -> Expected: {m['expected']}, Predicted: {m['predicted']}")

    # Enforce >=90% accuracy target (PART 17)
    assert accuracy >= 0.90, f"Router accuracy was {accuracy * 100:.2f}%, which is below the 90% required target."
