class RetryManager:
    """Manages exponential backoff retries."""

    def __init__(self, initial_delay: float = 1.0, max_delay: float = 3600.0):
        """
        Initialize the RetryManager.

        Args:
            initial_delay: Initial retry delay in seconds.
            max_delay: Maximum retry delay in seconds.
        """
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.current_delay = initial_delay

    def reset_delay(self):
        """Reset the retry delay to the initial value."""
        self.current_delay = self.initial_delay

    def increase_delay(self):
        """Double the retry delay, capping it at max_delay."""
        self.current_delay = min(self.current_delay * 2, self.max_delay)

    def get_current_delay(self) -> float:
        """Get the current retry delay."""
        return self.current_delay
