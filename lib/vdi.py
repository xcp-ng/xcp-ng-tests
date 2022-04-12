class VDI:
    def __init__(self, sr, uuid):
        self.uuid = uuid
        # TODO: use a different approach when migration is possible
        self.sr = sr

    def destroy(self):
        self.sr.pool.master.xe('vdi-destroy', {'uuid': self.uuid})
