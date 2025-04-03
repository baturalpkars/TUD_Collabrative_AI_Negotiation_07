import logging
from random import randint
from time import time
from typing import cast

from geniusweb.actions.Accept import Accept
from geniusweb.actions.Action import Action
from geniusweb.actions.Offer import Offer
from geniusweb.actions.PartyId import PartyId
from geniusweb.bidspace.AllBidsList import AllBidsList
from geniusweb.inform.ActionDone import ActionDone
from geniusweb.inform.Finished import Finished
from geniusweb.inform.Inform import Inform
from geniusweb.inform.Settings import Settings
from geniusweb.inform.YourTurn import YourTurn
from geniusweb.issuevalue.Bid import Bid
from geniusweb.issuevalue.Domain import Domain
from geniusweb.party.Capabilities import Capabilities
from geniusweb.party.DefaultParty import DefaultParty
from geniusweb.profile.utilityspace.LinearAdditiveUtilitySpace import (
    LinearAdditiveUtilitySpace,
)
from geniusweb.profileconnection.ProfileConnectionFactory import (
    ProfileConnectionFactory,
)
from geniusweb.progress.ProgressTime import ProgressTime
from geniusweb.references.Parameters import Parameters
from tudelft_utilities_logging.ReportToLogger import ReportToLogger

from .utils.opponent_model import OpponentModel


class TemplateAgent(DefaultParty):
    """
    Template of a Python geniusweb agent.
    """

    def __init__(self):
        super().__init__()
        self.logger: ReportToLogger = self.getReporter()

        self.domain: Domain = None
        self.parameters: Parameters = None
        self.profile: LinearAdditiveUtilitySpace = None
        self.progress: ProgressTime = None
        self.me: PartyId = None
        self.other: str = None
        self.settings: Settings = None
        self.storage_dir: str = None

        self.last_received_bid: Bid = None
        self.opponent_model: OpponentModel = None
        self.logger.log(logging.INFO, "party is initialized")

    def notifyChange(self, data: Inform):
        """MUST BE IMPLEMENTED
        This is the entry point of all interaction with your agent after is has been initialised.
        How to handle the received data is based on its class type.

        Args:
            info (Inform): Contains either a request for action or information.
        """

        # a Settings message is the first message that will be send to your
        # agent containing all the information about the negotiation session.
        if isinstance(data, Settings):
            self.settings = cast(Settings, data)
            self.me = self.settings.getID()

            # progress towards the deadline has to be tracked manually through the use of the Progress object
            self.progress = self.settings.getProgress()

            self.parameters = self.settings.getParameters()
            self.storage_dir = self.parameters.get("storage_dir")

            # the profile contains the preferences of the agent over the domain
            profile_connection = ProfileConnectionFactory.create(
                data.getProfile().getURI(), self.getReporter()
            )
            self.profile = profile_connection.getProfile()
            self.domain = self.profile.getDomain()
            profile_connection.close()

        # ActionDone informs you of an action (an offer or an accept)
        # that is performed by one of the agents (including yourself).
        elif isinstance(data, ActionDone):
            action = cast(ActionDone, data).getAction()
            actor = action.getActor()

            # ignore action if it is our action
            if actor != self.me:
                # obtain the name of the opponent, cutting of the position ID.
                self.other = str(actor).rsplit("_", 1)[0]

                # process action done by opponent
                self.opponent_action(action)
        # YourTurn notifies you that it is your turn to act
        elif isinstance(data, YourTurn):
            # execute a turn
            self.my_turn()

        # Finished will be send if the negotiation has ended (through agreement or deadline)
        elif isinstance(data, Finished):
            self.save_data()
            # terminate the agent MUST BE CALLED
            self.logger.log(logging.INFO, "party is terminating:")
            super().terminate()
        else:
            self.logger.log(logging.WARNING, "Ignoring unknown info " + str(data))

    def getCapabilities(self) -> Capabilities:
        """MUST BE IMPLEMENTED
        Method to indicate to the protocol what the capabilities of this agent are.
        Leave it as is for the ANL 2022 competition

        Returns:
            Capabilities: Capabilities representation class
        """
        return Capabilities(
            set(["SAOP"]),
            set(["geniusweb.profile.utilityspace.LinearAdditive"]),
        )

    def send_action(self, action: Action):
        """Sends an action to the opponent(s)

        Args:
            action (Action): action of this agent
        """
        self.getConnection().send(action)

    # give a description of your agent
    def getDescription(self) -> str:
        """MUST BE IMPLEMENTED
        Returns a description of your agent. 1 or 2 sentences.

        Returns:
            str: Agent description
        """
        return "Template agent of group 7 from TUD_CAI"

    def opponent_action(self, action):
        """Process an action that was received from the opponent.

        Args:
            action (Action): action of opponent
        """
        # if it is an offer, set the last received bid
        if isinstance(action, Offer):
            # create opponent model if it was not yet initialised
            if self.opponent_model is None:
                self.opponent_model = OpponentModel(self.domain)

            bid = cast(Offer, action).getBid()

            # Get normalized time (0 to 1) from the progress object
            if self.progress is not None:
                normalized_time = self.progress.get(time() * 1000)
            else:
                normalized_time = 0.0

            # update opponent model with bid
            self.opponent_model.update(bid, normalized_time)
            # set bid as last received
            self.last_received_bid = bid

    def my_turn(self):
        """This method is called when it is our turn. It should decide upon an action
        to perform and send this action to the opponent.
        """
        # check if the last received offer is good enough
        if self.accept_condition(self.last_received_bid):
            # if so, accept the offer
            action = Accept(self.me, self.last_received_bid)
        else:
            # if not, find a bid to propose as counter offer
            bid = self.find_bid()
            action = Offer(self.me, bid)

        # send the action
        self.send_action(action)

    def save_data(self):
        """This method is called after the negotiation is finished. It can be used to store data
        for learning capabilities. Note that no extensive calculations can be done within this method.
        Taking too much time might result in your agent being killed, so use it for storage only.
        """
        data = "Data for learning (see README.md)"
        with open(f"{self.storage_dir}/data.md", "w") as f:
            f.write(data)

    ###########################################################################################
    ################################## Example methods below ##################################
    ###########################################################################################
    def get_target_utility(self, progress: float, beta: float = 0.2) -> float:
        """
        Computes a time-dependent utility threshold.
        Boulware (beta < 1): high utility until late rounds.
        Conceder (beta > 1): faster concession.
        """
        return 1 - (progress ** (1 / beta))

    def accept_condition(self, bid: Bid) -> bool:
        if bid is None:
            return False

        # progress of the negotiation session between 0 and 1 (1 is deadline)
        progress = self.progress.get(time() * 1000)
        my_util = float(self.profile.getUtility(bid))
        target_util = self.get_target_utility(progress, beta=0.2)

        # Optional: Predict our next bid's utility
        planned_bid = self.find_bid()
        planned_util = float(self.profile.getUtility(planned_bid))

        # === 🛡️ Reservation Utility Threshold ===
        # Option 1: Static threshold (e.g. never accept below 0.4)
        # if my_util < 0.4:
        #     return False

        # Option 2: Dynamic threshold — becomes softer as time progresses
        reservation_threshold = 0.6 - 0.4 * progress
        if my_util < reservation_threshold:
            return False

        # Accept if offer is better than what we would offer with a better threshold
        return my_util >= max(target_util, 0.85 * planned_util)

    def find_bid(self) -> Bid:
        domain = self.profile.getDomain()
        all_bids = AllBidsList(domain)

        progress = self.progress.get(time() * 1000)
        target_util = self.get_target_utility(progress, beta=0.2)
        margin = 0.05  # accept bids within this range

        candidate_bids = []

        for _ in range(1000):
            bid = all_bids.get(randint(0, all_bids.size() - 1))
            util = float(self.profile.getUtility(bid))
            if abs(util - target_util) <= margin:
                candidate_bids.append(bid)

        if not candidate_bids:
            return all_bids.get(randint(0, all_bids.size() - 1))

        best_bid = max(candidate_bids, key=self.score_bid)

        return best_bid

    def score_bid(self, bid: Bid, alpha_start: float = 0.95, alpha_end: float = 0.5, eps: float = 0.1) -> float:
        """
        Calculates a dynamic heuristic score for a bid.

        - Early in negotiation: prioritize self-utility (high alpha).
        - Late in negotiation: reduce alpha to care more about opponent.
        - Uses time pressure (via eps) to adjust behavior over time.

        Args:
            bid (Bid): Bid to evaluate.
            alpha_start (float): Starting weight for self-utility.
            alpha_end (float): Ending weight for self-utility.
            eps (float): Controls time pressure sensitivity.

        Returns:
            float: Composite score.
        """
        progress = self.progress.get(time() * 1000)

        # Linearly interpolate alpha over time
        alpha = alpha_start - (alpha_start - alpha_end) * progress

        # Your own utility
        our_utility = float(self.profile.getUtility(bid))

        # Time pressure: slows down concessions at first
        time_pressure = 1.0 - progress ** (1 / eps)

        score = alpha * time_pressure * our_utility

        # Consider opponent utility more over time
        if self.opponent_model is not None:
            opponent_utility = self.opponent_model.get_predicted_utility(bid)
            opponent_score = (1.0 - alpha) * time_pressure * opponent_utility
            score += opponent_score

        return score
