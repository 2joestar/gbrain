class AdaptiveLearner:

    def __init__(self):
        self.stats = {}

    def update(self, provider, success, latency=None):
        if provider not in self.stats:
            self.stats[provider] = {"success": 0, "fail": 0, "total_latency": 0.0, "count": 0}
        if success:
            self.stats[provider]["success"] += 1
        else:
            self.stats[provider]["fail"] += 1
        if latency is not None:
            self.stats[provider]["total_latency"] += latency
            self.stats[provider]["count"] += 1

    def score(self, provider):
        s = self.stats.get(provider, {"success": 1, "fail": 1, "total_latency": 0.0, "count": 0})
        success_rate = s["success"] / (s["success"] + s["fail"])
        # FIX 3: score = success_rate / max(latency, 0.001) to prevent division by zero
        latency = s["count"]
        return (success_rate + 0.1) / max((s["total_latency"] / max(latency, 1)), 0.001)  # Cold Start Bias