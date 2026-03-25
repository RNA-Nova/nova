from pi_agent import Agent


class BioAgent:

    def __init__(
        self,
        steering_mode = "one-at-time",
        follow_up_mode = "one-at-time",
                 

    ):
        self.agent = Agent(
            initial_state = None,
            convert_to_llm = None,
            transform_context = None,
            steering_mode = "one-at-a-time",
            follow_up_mode = "one-at-a-time",
            stream_fn = None,
            get_api_key = None,
        )
