class SimulationClockNotInitializedError(Exception):
    pass


class IdempotencyKeyReusedError(Exception):
    pass

class OrderNotFoundError(Exception):
    pass


class OrderNotEditableError(Exception):
    pass

class ShipmentNotFoundError(Exception):
    pass

class InvalidShipmentStateError(Exception):
    pass

class NetworkDataNotInitializedError(Exception):
    pass

class TransportTaskNotFoundError(Exception):
    pass


class InvalidExpectedArrivalError(Exception):
    pass


class InvalidTaskShipmentError(Exception):
    pass

class InvalidTransportTaskStateError(Exception):
    pass

class NetworkError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)
