"""
Goal model sketch. Core design decisions baked in here (write these into
DECISIONS.md in your own words):

1. Rules aren't equal-weight. BLOCKING rules are gatekeepers -- if one
   fails, the verdict is AVOID no matter what else is true. BONUS rules
   only matter once every blocking rule has passed; they can upgrade a
   verdict from SUITABLE to RECOMMENDED, but can never rescue a blocking
   failure.

2. Verdicts are informational tiers (AVOID / SUITABLE / RECOMMENDED /
   UNKNOWN), not directives. This tool states facts relative to a goal --
   it doesn't tell the user what to do. That framing needs to carry
   through to whatever text gets shown to the user later.

3. Every rule result carries a confidence level (direct / estimated /
   unknown), reflecting whether we had the real nutrient data, used a
   fallback proxy, or had nothing usable at all.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable
from normalize import Food


class RuleRole(Enum):
    BLOCKING = "blocking"      # must pass, or overall verdict = AVOID
    MODERATION = "moderation"  # matters, but shouldn't hard-block -- downgrades to MODERATE instead
    BONUS = "bonus"            # only matters if all blocking rules passed


class Comparison(Enum):
    MAX = "max"  # value must be <= threshold
    MIN = "min"  # value must be >= threshold


class Confidence(Enum):
    DIRECT = "direct"        # real data for the target nutrient
    ESTIMATED = "estimated"  # fallback nutrient used as a proxy
    UNKNOWN = "unknown"      # neither target nor fallback available


class Tier(Enum):
    AVOID = "avoid"
    CAUTION = "caution"        # blocking data unknown, but a bonus rule failed clearly
    MODERATE = "moderate"      # blocking rules fine, but a moderation-role rule failed -- eat in moderation
    SUITABLE = "suitable"
    RECOMMENDED = "recommended"
    UNKNOWN = "unknown"        # no usable signal at all, blocking or bonus


# REMOVED: PortionType / infer_portion_type (see DECISIONS.md).
#
# Tried a category-keyword heuristic to distinguish "snack" from
# "ingredient" (e.g. olive oil, flour) so calorie rules wouldn't misjudge
# raw cooking ingredients. Found it fundamentally unreliable via testing:
# basmati rice, and likely many other common staples, aren't in any
# keyword list -- and a keyword list can never really be complete, since
# there are hundreds of raw-ingredient categories. A brittle heuristic
# that fails on common foods is worse than not having it.
#
# The actual root problem was narrower than "snack vs ingredient": a food
# only gets misjudged when its serving_size_g is missing, forcing
# per_serving() to fall back to judging the full 100g density (nobody
# eats 100g of olive oil at once, but 14g of it is fine). That fallback
# is already visible and honest -- every verdict's reasoning shows its
# basis ("per_serving (43g)" vs "per_100g (serving size unknown)"), so
# the user isn't misled, they just see why a judgment might look strict.
# Fixing the guess with more guessing wasn't worth it; the existing
# honesty mechanism already covers this.


@dataclass
class Rule:
    nutrient: str                 # field name on Food, e.g. "cholesterol_mg"
    comparison: Comparison
    role: RuleRole                # default/fallback role, used when processing status is unknown
    label: str                    # human-readable name, e.g. "Cholesterol"
    threshold: Optional[float] = None          # flat threshold (per serving)
    fallback_nutrient: Optional[str] = None
    fallback_threshold: Optional[float] = None
    # Ratio-based threshold: for guidance that's inherently a RATIO (like
    # ADA's "14g fiber per 1000kcal"), not a flat per-serving number. When
    # set, the actual threshold is computed per-food from its own calorie
    # content, instead of using one fixed number for every food regardless
    # of how many calories it has. Exactly one of `threshold` or
    # `ratio_per_1000kcal` should be set on a given Rule.
    ratio_per_1000kcal: Optional[float] = None

    # Optional precondition: (nutrient, min_threshold). The rule only
    # applies if that nutrient meets the threshold. E.g. a fiber rule for
    # diabetes only makes sense for foods with meaningful carbs -- fiber's
    # whole role is slowing carb/sugar absorption, so a zero-carb food
    # (like plain chicken) doesn't need to clear a fiber bar to be
    # diabetes-friendly. Without this, foods get penalized for lacking a
    # nutrient that was never relevant to them in the first place.
    applies_when: Optional[tuple] = None

    # Conditional role override based on food processing level. E.g.
    # saturated fat should BLOCK for a processed snack but only warrant
    # MODERATION for a whole food like salmon or chicken, where some
    # saturated fat is a natural part of the food rather than an added
    # ingredient. Found via testing: salmon (naturally ~3g saturated fat
    # per 100g, from healthy omega-3-rich fish) was wrongly AVOID-ed by
    # the same flat threshold used to correctly flag potato chips. When
    # food.is_processed is unknown (None), falls back to `role` above
    # rather than guessing which one applies.
    role_when_whole: Optional[RuleRole] = None
    role_when_processed: Optional[RuleRole] = None

    # Alternate pass condition: a second, independent way to pass this
    # rule, checked only if the primary (per-serving) check fails. Used
    # when a food's realistic serving size distorts an absolute per-
    # serving judgment -- e.g. almonds have a small standardized serving
    # (calorie-dense foods do), so their per-serving protein grams look
    # unimpressive even though their protein DENSITY is genuinely good.
    # Black beans have a large standardized serving, so their per-serving
    # calories look high even though their calorie DENSITY is genuinely
    # low. Both alt checks below are sourced from real, independent
    # standards (EU protein-by-energy-% claims; Rolls' energy-density
    # weight-management research) -- not made up to force a specific food
    # to pass. A food only needs to clear ONE of the two checks.
    alt_pass_check: Optional[Callable[["Food"], bool]] = None
    alt_pass_label: Optional[str] = None  # shown when the alt check is what saved the verdict

    def effective_role(self, food: Food) -> RuleRole:
        if food.is_processed is True and self.role_when_processed is not None:
            return self.role_when_processed
        if food.is_processed is False and self.role_when_whole is not None:
            return self.role_when_whole
        return self.role

    def _resolve_threshold(self, serving_view: dict) -> Optional[float]:
        """Figure out the actual threshold to use for this evaluation.
        Ratio-based thresholds are computed from the food's own calorie
        count."""
        if self.ratio_per_1000kcal is not None:
            calories = serving_view.get("calories")
            if calories is None:
                return None  # can't compute a ratio-based threshold without calories
            return (calories / 1000) * self.ratio_per_1000kcal

        return self.threshold

    def evaluate(self, food: Food) -> "RuleResult":
        # Real-world thresholds (FDA claims, AHA, ADA) are virtually all
        # defined PER SERVING, not per 100g -- so we evaluate against the
        # per-serving view, not the raw per-100g fields. When serving size
        # is unknown, per_serving() falls back to per-100g values itself
        # and flags that in its 'basis' key, which we surface in the reason
        # (this is the honesty mechanism that replaced portion-type
        # guessing -- see the comment above PortionType's removal).
        serving_view = food.per_serving()
        basis_note = serving_view["basis"]

        # Precondition check: does this rule even apply to this food?
        # IMPORTANT: checked against the food's per-100g COMPOSITION
        # (getattr(food, ...)), not the serving-scaled value from
        # serving_view. Found via testing: pure sugar has a tiny realistic
        # serving (~4g), so its per-serving carbs looked "minimal" even
        # though sugar is ~100% carbs by composition -- letting it dodge
        # the fiber requirement entirely and get a false RECOMMENDED for
        # Diabetes Management. Composition ("is this fundamentally a
        # carb-containing food?") is the actual question this precondition
        # is meant to answer, and that's a property of the food itself,
        # not of how small a typical serving happens to be.
        if self.applies_when is not None:
            precondition_nutrient, precondition_min = self.applies_when
            precondition_value = getattr(food, precondition_nutrient, None)
            # If we can't confirm the precondition (missing data), default
            # to "applies" -- better to evaluate normally than to silently
            # exempt a food we don't actually have enough info about.
            if precondition_value is not None and precondition_value < precondition_min:
                return RuleResult(
                    rule=self, passed=None, confidence=Confidence.DIRECT,
                    applicable=False, resolved_role=self.effective_role(food),
                    reason=(f"{self.label}: not applicable (this food has minimal "
                             f"{precondition_nutrient}, so this check doesn't meaningfully apply)."),
                )

        value = serving_view.get(self.nutrient)
        confidence = Confidence.DIRECT
        used_nutrient = self.nutrient
        used_threshold = self._resolve_threshold(serving_view)

        if value is None and self.fallback_nutrient:
            value = serving_view.get(self.fallback_nutrient)
            confidence = Confidence.ESTIMATED
            used_nutrient = self.fallback_nutrient
            used_threshold = self.fallback_threshold

        if value is None or used_threshold is None:
            missing_what = "nutrient data" if value is None else "calorie data needed to compute the threshold"
            return RuleResult(
                rule=self, passed=None, confidence=Confidence.UNKNOWN,
                resolved_role=self.effective_role(food),
                reason=f"{self.label}: no data available ({missing_what})."
            )

        if self.comparison == Comparison.MAX:
            passed = value <= used_threshold
            comparator_word = "exceeds" if not passed else "within"
        else:
            passed = value >= used_threshold
            comparator_word = "below" if not passed else "meets"

        estimate_note = " (estimated from a related nutrient)" if confidence == Confidence.ESTIMATED else ""
        reason = f"{self.label}: {value}{estimate_note} {comparator_word} your {round(used_threshold, 2)} limit [{basis_note}]."

        # If the primary (per-serving) check failed, give the alternate
        # density-based check a chance to rescue it -- see the comment
        # above alt_pass_check for why this exists.
        if not passed and self.alt_pass_check is not None and self.alt_pass_check(food):
            passed = True
            reason += f" Passes on an alternate basis: {self.alt_pass_label}."

        return RuleResult(
            rule=self, passed=passed, confidence=confidence, reason=reason,
            resolved_role=self.effective_role(food),
            value=round(value, 2), threshold=round(used_threshold, 2),
            unit=_unit_for(used_nutrient), comparison=self.comparison.value,
        )


@dataclass
class RuleResult:
    rule: Rule
    passed: Optional[bool]   # None means "couldn't evaluate"
    confidence: Confidence
    reason: str
    applicable: bool = True   # False means this rule was skipped as not relevant to this food
    resolved_role: Optional[RuleRole] = None  # the role actually used for THIS food (may differ from rule.role)
    # Structured fields, for a caller (like a frontend) that wants to build
    # its own readable presentation instead of parsing `reason`. `reason`
    # stays around for quick/CLI use, but a real UI shouldn't have to
    # parse a sentence to get the actual number and limit back out.
    value: Optional[float] = None
    threshold: Optional[float] = None
    unit: str = ""
    comparison: Optional[str] = None  # "max" or "min"


def _unit_for(nutrient: str) -> str:
    if nutrient == "calories":
        return "kcal"
    if nutrient.endswith("_mg"):
        return "mg"
    if nutrient.endswith("_g"):
        return "g"
    return ""


@dataclass
class Goal:
    name: str
    rules: list[Rule] = field(default_factory=list)

    def evaluate(self, food: Food) -> "GoalResult":
        results = [rule.evaluate(food) for rule in self.rules]

        # Non-applicable rules (e.g. fiber for a zero-carb food) are shown
        # in the output for transparency, but excluded from tier math --
        # a rule that doesn't apply shouldn't count as a fail OR a pass.
        # Grouped by resolved_role, not rule.role directly, since a rule's
        # effective role can depend on the specific food (e.g. saturated
        # fat blocks for processed snacks but only moderates for whole
        # foods like salmon).
        blocking_results = [r for r in results if r.resolved_role == RuleRole.BLOCKING and r.applicable]
        moderation_results = [r for r in results if r.resolved_role == RuleRole.MODERATION and r.applicable]
        bonus_results = [r for r in results if r.resolved_role == RuleRole.BONUS and r.applicable]

        # If any blocking rule definitively fails, it's AVOID -- nothing
        # else can override that, per the priority you specified.
        if any(r.passed is False for r in blocking_results):
            tier = Tier.AVOID
        # If a blocking rule couldn't be evaluated, we shouldn't claim
        # confidence we don't have -- but we also shouldn't discard real
        # signal we DO have from a bonus rule that clearly failed. E.g.
        # fiber (blocking) unknown + sugar (bonus) way over threshold is
        # meaningfully different from fiber unknown + no other signal at
        # all. CAUTION surfaces that partial signal instead of hiding it
        # behind a blanket "unknown."
        elif any(r.passed is None for r in blocking_results):
            if any(r.passed is False for r in bonus_results):
                tier = Tier.CAUTION
            else:
                tier = Tier.UNKNOWN
        # Blocking is clear -- a failed MODERATION rule (e.g. cholesterol,
        # per current AHA guidance treating it as a factor to moderate
        # rather than a hard cutoff) downgrades to MODERATE, softer than
        # AVOID but not a clean pass either.
        elif any(r.passed is False for r in moderation_results):
            tier = Tier.MODERATE
        # Everything blocking/moderation is fine -- bonus rules decide
        # SUITABLE vs RECOMMENDED.
        elif any(r.passed is True for r in bonus_results):
            tier = Tier.RECOMMENDED
        else:
            tier = Tier.SUITABLE

        return GoalResult(goal=self, tier=tier, rule_results=results)


@dataclass
class GoalResult:
    goal: Goal
    tier: Tier
    rule_results: list[RuleResult]


# Worst-to-best ordering used ONLY when a user has multiple goals active
# at once and needs one combined verdict across all of them. This is NOT
# sourced from any medical/regulatory guidance -- there's no external
# standard for "how do you combine unrelated dietary goals into one
# answer." This is our own reasoned design:
#   - AVOID and CAUTION (a confirmed or partially-confirmed concern)
#     always outrank not knowing anything.
#   - UNKNOWN ranks below MODERATE deliberately: a confirmed-but-minor
#     issue (Moderate) at least tells you something specific, whereas
#     Unknown on a goal you specifically care about tells you nothing --
#     it shouldn't be silently beaten by a small known issue elsewhere.
COMBINED_SEVERITY_ORDER = [
    Tier.AVOID, Tier.CAUTION, Tier.UNKNOWN, Tier.MODERATE, Tier.SUITABLE, Tier.RECOMMENDED,
]


def combine_results(results_by_goal: dict[str, "GoalResult"]) -> tuple[Tier, Optional[str]]:
    """Given multiple goals' results for the same food, return the single
    worst tier (per COMBINED_SEVERITY_ORDER) and which goal drove it.
    If there's a tie, the first goal (in the order given) with that tier
    is reported as the driver -- deterministic, not arbitrary, but not
    meaningful beyond "which one happened to be checked first."""
    worst_goal_name = None
    worst_tier = Tier.RECOMMENDED
    worst_rank = COMBINED_SEVERITY_ORDER.index(Tier.RECOMMENDED)

    for goal_name, result in results_by_goal.items():
        rank = COMBINED_SEVERITY_ORDER.index(result.tier)
        if rank < worst_rank:
            worst_rank = rank
            worst_tier = result.tier
            worst_goal_name = goal_name

    return worst_tier, worst_goal_name


if __name__ == "__main__":
    # Sketch: "High Cholesterol Management" goal
    high_cholesterol_goal = Goal(
        name="High Cholesterol Management",
        rules=[
            Rule(
                nutrient="cholesterol_mg", comparison=Comparison.MAX,
                threshold=20, role=RuleRole.BLOCKING, label="Cholesterol",
                fallback_nutrient="saturated_fat_g", fallback_threshold=5,
            ),
            Rule(
                nutrient="fiber_g", comparison=Comparison.MIN,
                threshold=5, role=RuleRole.BONUS, label="Fiber",
            ),
        ],
    )

    # Reuse the real Nutella data shape from earlier testing --
    # missing cholesterol, this is exactly the case the fallback exists for.
    nutella = Food(
        name="Nutella", source="off", serving_size_g=None,
        calories=539, protein_g=6.3, total_fat_g=30.9,
        saturated_fat_g=10.6, carbs_g=57.5, sugar_g=56.3,
        fiber_g=None, sodium_mg=41, cholesterol_mg=None,
    )

    result = high_cholesterol_goal.evaluate(nutella)
    print(f"Tier: {result.tier.value}")
    for r in result.rule_results:
        print(f"  [{r.confidence.value}] {r.reason}")