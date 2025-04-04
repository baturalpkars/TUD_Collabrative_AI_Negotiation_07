from collections import defaultdict

from geniusweb.issuevalue.Bid import Bid
from geniusweb.issuevalue.DiscreteValueSet import DiscreteValueSet
from geniusweb.issuevalue.Domain import Domain
from geniusweb.issuevalue.Value import Value


class OpponentModel:
    def __init__(self, domain: Domain):
        self.offers = []
        self.domain = domain
        self.consistency_score = 0.0

        self.issue_estimators = {
            i: IssueEstimator(v) for i, v in domain.getIssuesValues().items()
        }

        self.my_utilities = []  # Utility of their bids from our perspective
        self.is_conceder = False  # Flag
        self.is_stubborn = False  # Flag
        self.in_deadlock = False
        self.late_utilities = []  # Utilities between 0.85–0.95
        self.final_utilities = []  # Utilities after 0.95
        self.is_late_conceder = False  # New flag for Boulware-like late concession

    def update(self, bid: Bid, time: float, our_utility_func=None):
        if self.offers:
            prev_bid = self.offers[-1]
            similarity = self._calculate_similarity(prev_bid, bid)
            self.consistency_score += similarity

        # keep track of all bids received
        self.offers.append(bid)

        if our_utility_func:
            self.my_utilities.append(our_utility_func(bid))

            if 0.85 <= time < 0.95:
                self.late_utilities.append(our_utility_func(bid))
            elif time >= 0.95:
                self.final_utilities.append(our_utility_func(bid))

            self._detect_late_conceder()
            self._detect_conceder()
            self._detect_boulware()
            self._detect_hardliner()
            self._detect_deadlock()

        # print(f"\n[OpponentModel] Time: {time:.2f}")
        # print(f"[OpponentModel] Consistency score: {self.consistency_score:.4f}")
        # print("[OpponentModel] Current issue weights:")

        # update all issue estimators with the value that is offered for that issue
        for issue_id, issue_estimator in self.issue_estimators.items():
            issue_estimator.update(bid.getValue(issue_id), time)

    # Detects if the opponent is conceding by checking if the utility has increased
    def _detect_conceder(self, window=5, threshold=0.2):
        if len(self.my_utilities) >= 2 * window:
            early = sum(self.my_utilities[:window]) / window
            recent = sum(self.my_utilities[-window:]) / window
            if (recent - early) >= threshold:
                self.is_conceder = True

    def _detect_late_conceder(self, threshold=0.05):
        if len(self.late_utilities) >= 2 and len(self.final_utilities) >= 2:
            avg_late = sum(self.late_utilities) / len(self.late_utilities)
            avg_final = sum(self.final_utilities) / len(self.final_utilities)
            delta = avg_final - avg_late

            if delta > threshold:
                self.is_late_conceder = True

    # If the recent average is greater than the early average, it's a Boulware -- stubborn
    def _detect_boulware(self, window=5, threshold=0.1):
        # Compares early and recent windows. A slight concession increase indicates Boulware
        if len(self.my_utilities) >= 2 * window:
            early_avg = sum(self.my_utilities[:window]) / window
            recent_avg = sum(self.my_utilities[-window:]) / window
            delta = recent_avg - early_avg

            if 0 < delta < threshold:
                self.is_stubborn = True

    # If the recent average is less than the early average, it's a hardliner -- stubborn
    def _detect_hardliner(self, window=5, threshold=0.05):
        if len(self.my_utilities) >= 2 * window:
            early_avg = sum(self.my_utilities[:window]) / window
            recent_avg = sum(self.my_utilities[-window:]) / window
            delta = recent_avg - early_avg

            if delta < threshold:
                self.is_stubborn = True

    # Detects if the opponent is in a deadlock by checking if the utility has not changed
    def _detect_deadlock(self, window=6, threshold=0.01):
        if len(self.my_utilities) < window:
            return
        recent = self.my_utilities[-window:]
        diffs = [abs(recent[i] - recent[i - 1]) for i in range(1, len(recent))]
        if all(d < threshold for d in diffs):
            self.in_deadlock = True

    def _calculate_similarity(self, bid1: Bid, bid2: Bid):
        matches = 0
        for issue in self.domain.getIssues():
            if bid1.getValue(issue) == bid2.getValue(issue):
                matches += 1
        return matches / len(self.domain.getIssues())

    def get_predicted_utility(self, bid: Bid):
        if len(self.offers) == 0 or bid is None:
            return 0

        # initiate
        total_issue_weight = 0.0
        value_utilities = []
        issue_weights = []

        for issue_id, issue_estimator in self.issue_estimators.items():
            # get the value that is set for this issue in the bid
            value: Value = bid.getValue(issue_id)

            # collect both the predicted weight for the issue and
            # predicted utility of the value within this issue
            value_utilities.append(issue_estimator.get_value_utility(value))
            issue_weights.append(issue_estimator.weight)

            total_issue_weight += issue_estimator.weight

        # normalise the issue weights such that the sum is 1.0
        if total_issue_weight == 0.0:
            issue_weights = [1 / len(issue_weights) for _ in issue_weights]
        else:
            issue_weights = [iw / total_issue_weight for iw in issue_weights]

        # calculate predicted utility by multiplying all value utilities with their issue weight
        predicted_utility = sum(
            [iw * vu for iw, vu in zip(issue_weights, value_utilities)]
        )

        # print(f"[OpponentModel] Predicted utility for bid {bid.getIssueValues()}: {predicted_utility:.4f}")

        return predicted_utility

    def to_json(self):
        return {
            "is_conceder": self.is_conceder,
            "issue_weights": {
                issue: {
                    "weight": est.weight,
                    "value_counts": {
                        str(val): est.value_trackers[val].count
                        for val in est.value_trackers
                    }
                }
                for issue, est in self.issue_estimators.items()
            }
        }

    def load_from_json(self, data: dict):
        self.is_conceder = data.get("is_conceder", False)
        for issue, issue_data in data.get("issue_weights", {}).items():
            if issue not in self.issue_estimators:
                continue
            est = self.issue_estimators[issue]
            est.weight = issue_data.get("weight", 0.0)
            for val_str, count in issue_data.get("value_counts", {}).items():

                for val in est.value_trackers:
                    if str(val) == val_str:
                        est.value_trackers[val].count = count


class IssueEstimator:
    def __init__(self, value_set: DiscreteValueSet):
        if not isinstance(value_set, DiscreteValueSet):
            raise TypeError(
                "This issue estimator only supports issues with discrete values"
            )

        self.bids_received = 0
        self.max_value_count = 0
        self.num_values = value_set.size()
        self.value_trackers = defaultdict(ValueEstimator)
        self.weight = 0
        self.time_series = defaultdict(list)

    def update(self, value: Value, time: float):
        self.bids_received += 1

        # get the value tracker of the value that is offered
        value_tracker = self.value_trackers[value]

        # register that this value was offered
        value_tracker.update(time)

        # update the count of the most common offered value
        self.max_value_count = max([value_tracker.count, self.max_value_count])

        # update predicted issue weight
        # the intuition here is that if the values of the receiverd offers spread out over all
        # possible values, then this issue is likely not important to the opponent (weight == 0.0).
        # If all received offers proposed the same value for this issue,
        # then the predicted issue weight == 1.0
        equal_shares = self.bids_received / self.num_values
        self.weight = (self.max_value_count - equal_shares) / (
            self.bids_received - equal_shares
        )

        # recalculate all value utilities
        for value_tracker in self.value_trackers.values():
            value_tracker.recalculate_utility(self.max_value_count, self.weight, time)

        self.time_series[value].append(time)

        # Estimate time-based concession
        recent_times = self.time_series[value][-3:]
        if len(recent_times) >= 2:
            time_diff = recent_times[-1] - recent_times[0]
            # Increase concession weight if value was offered at later stage
            if time_diff > 0.3:
                self.weight *= 0.9  # Penalize issue if it's changing often late-game

    def get_value_utility(self, value: Value):
        if value in self.value_trackers:
            return self.value_trackers[value].utility

        return 0


class ValueEstimator:
    def __init__(self):
        self.count = 0
        self.utility = 0
        self.last_time = 0

    def update(self, time: float):
        self.count += 1
        self.last_time = time

    def recalculate_utility(self, max_value_count: int, weight: float, current_time: float):
        time_decay = 1 / (1 + (current_time - self.last_time))
        if weight < 1:
            mod_value_count = ((self.count + 1) ** (1 - weight)) - 1
            mod_max_value_count = ((max_value_count + 1) ** (1 - weight)) - 1

            self.utility = mod_value_count / mod_max_value_count * time_decay
        else:
            self.utility = 1
