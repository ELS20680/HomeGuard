import os, csv, datetime

class DailyCsvLogger:
    def __init__(self, data_dir, prefix, tz="UTC", flush_every=5):
        self.data_dir = data_dir; self.prefix = prefix; self.flush_every = flush_every
        os.makedirs(self.data_dir, exist_ok=True)
        self._date = None; self._f = None; self._w = None; self._n = 0
        self.tz = tz

    def _roll(self):
        d = datetime.date.today().strftime("%Y-%m-%d")
        if d != self._date:
            if self._f: self._f.close()
            path = os.path.join(self.data_dir, f"{d}_{self.prefix}.csv")
            self._f = open(path, "a", newline="")
            self._w = csv.writer(self._f)
            if self._f.tell() == 0:
                self._w.writerow(["ts_iso","temp_c","humidity_pct","motion","fan_on","light_on","mode","image_path"])
            self._date = d; self._n = 0

    def write(self, ts_iso, temp, hum, motion, fan_on, light_on, mode, image_path):
        self._roll()
        self._w.writerow([ts_iso, temp, hum, motion, fan_on, light_on, mode, image_path or ""])
        self._n += 1
        if self._n % self.flush_every == 0:
            self._f.flush()

    def close(self):
        if self._f: self._f.close()
