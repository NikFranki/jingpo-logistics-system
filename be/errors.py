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