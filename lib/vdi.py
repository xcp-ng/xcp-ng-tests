import logging

class VDI:
    def __init__(self, sr, uuid, snapshot_of=None):
        self.uuid = uuid
        self.snapshot_of = snapshot_of
        # TODO: use a different approach when migration is possible
        self.sr = sr

    def snapshot(self):
        logging.info(f"Create snapshot of {self}")
        return VDI(self.sr,
                   self.sr.pool.master.xe('vdi-snapshot', {'uuid': self.uuid}),
                   snapshot_of=self)

    def clone(self):
        logging.info(f"Create clone of {self}")
        return VDI(self.sr,
                   self.sr.pool.master.xe('vdi-clone', {'uuid': self.uuid}))

    def destroy(self):
        logging.info("Destroy %s", self)
        self.sr.pool.master.xe('vdi-destroy', {'uuid': self.uuid})

    def __str__(self):
        return (f"VDI {self.uuid} on SR {self.sr.uuid}"
                f"{f' (snapshot of {self.snapshot_of})' if self.snapshot_of else ''}")
