"""
Interactive CLI -- the real user-facing entry point for the project.
Ties together: fetcher.py (live data) -> goal evaluation -> db.py
(cache + history). Run with: python3 cli.py
"""

import requests
import db
from fetcher import fetch_usda_foods, fetch_off_product
from goals import GOALS
from goal import GoalResult
from normalize import Food


def choose_goal() -> str:
    goal_names = list(GOALS.keys())
    print("\nGoals:")
    for i, name in enumerate(goal_names, 1):
        print(f"  {i}. {name}")
    while True:
        choice = input("Pick a goal (number): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(goal_names):
            return goal_names[int(choice) - 1]
        print("Not a valid choice, try again.")


def show_result(food: Food, goal_name: str):
    result: GoalResult = GOALS[goal_name].evaluate(food)
    print(f"\n{food.name} vs. {goal_name}")
    print(f"Verdict: {result.tier.value.upper()}")
    reasons = []
    for r in result.rule_results:
        tag = "not applicable" if not r.applicable else r.confidence.value
        print(f"  [{tag}] {r.reason}")
        reasons.append(r.reason)

    db.log_evaluation(food, goal_name, result.tier.value, reasons)


def search_flow():
    query = input("Search for a food (USDA): ").strip()
    if not query:
        return
    try:
        results = fetch_usda_foods(query, page_size=5)
    except requests.exceptions.RequestException as e:
        print(f"Network error reaching USDA: {e}")
        print("Check your connection or API key and try again.")
        return

    if not results:
        print(f"No results found for '{query}'.")
        return

    print("\nResults:")
    for i, f in enumerate(results, 1):
        print(f"  {i}. {f.name}")
    choice = input("Pick a result (number): ").strip()
    if not (choice.isdigit() and 1 <= int(choice) <= len(results)):
        print("Not a valid choice.")
        return

    food = results[int(choice) - 1]
    goal_name = choose_goal()
    show_result(food, goal_name)


def barcode_flow():
    barcode = input("Enter a barcode (Open Food Facts): ").strip()
    if not barcode:
        return
    try:
        food = fetch_off_product(barcode)
    except requests.exceptions.RequestException as e:
        print(f"Network error reaching Open Food Facts: {e}")
        print("Check your connection and try again.")
        return

    if food is None:
        print(f"No product found for barcode '{barcode}'.")
        return

    goal_name = choose_goal()
    show_result(food, goal_name)


def history_flow():
    entries = db.get_recent_history(limit=10)
    if not entries:
        print("No history yet.")
        return
    print("\nRecent checks:")
    for e in entries:
        print(f"  {e['evaluated_at'][:19]}  {e['food_name']} vs {e['goal_name']} -> {e['tier'].upper()}")


def main():
    db.init_db()
    print("=== Food Goal Checker ===")

    while True:
        print("\n1. Search food by name")
        print("2. Look up food by barcode")
        print("3. View recent history")
        print("4. Quit")
        choice = input("> ").strip()

        if choice == "1":
            search_flow()
        elif choice == "2":
            barcode_flow()
        elif choice == "3":
            history_flow()
        elif choice == "4":
            print("Bye.")
            break
        else:
            print("Not a valid choice.")


if __name__ == "__main__":
    main()
