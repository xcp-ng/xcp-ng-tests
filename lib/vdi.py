import logging

class VDI:
    def __init__(self, sr, uuid):
        self.uuid = uuid
        # TODO: use a different approach when migration is possible
        self.sr = sr

    def destroy(self):
        logging.info("Destroy %s", self)
        self.sr.pool.master.xe('vdi-destroy', {'uuid': self.uuid})

    def __str__(self):
        return f"VDI {self.uuid} on SR {self.sr.uuid}"
