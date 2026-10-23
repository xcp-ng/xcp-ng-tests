<#
.SYNOPSIS
Raw stamped-block corruption detector for a dedicated, RAW, non-system test disk.

.DESCRIPTION
Writes and verifies 4 KiB "stamped" blocks directly on \\.\PhysicalDriveN with unbuffered,
write-through I/O. Each block carries a header (magic, run id, block LBA, generation, write
sequence number, seed), a pseudo-random payload derived from the seed, and a CRC32 over header
and payload. Every completed I/O is appended to C:\blockstamp\<run>.ledger so that `verify` can
classify each block as OK, STALE, MISDIRECTED, CRC, FUTURE or UNWRITTEN.

All LBAs, -Start, -Count and -Lba values are in 4 KiB block units. Block N starts at byte
N * 4096, i.e. at 512-byte sector N * 8. The first 256 blocks (1 MiB) are reserved and never
touched, so that partition-table probing can never find anything in them.

Safety: before opening any handle, the script refuses disks that are not RAW, that have
partitions, that are system/boot disks, that host C:, or whose size differs from -ExpectSize.
Load is bounded: -Threads <= 4, -IoSize <= 1 MiB, -Seconds <= 300.

-Mode selftest exercises encode/decode/classify on in-memory buffers and never touches a disk.

Output: one "PROBLEM ..." line per problem block (capped), then one line
"BLOCKSTAMP-RESULT <compressed json>". The JSON is also written to -Out when given.
#>
[CmdletBinding()]
param (
    [Parameter(Mandatory)]
    [ValidateSet("fill", "load", "write-sync", "verify", "reset-device", "selftest")]
    [string]$Mode,
    [int]$Disk = -1,
    [string]$Run = "",
    [long]$Start = 0,
    [long]$Count = 0,
    [long]$Lba = -1,
    [int]$Gen = 0,
    [int]$Threads = 1,
    [int]$IoSize = 262144,
    [int]$Seconds = 60,
    [int]$ReadPercent = 50,
    [switch]$Flush,
    [long]$ExpectSize = 0,
    [string]$Out = "",
    [string]$Dir = "C:\blockstamp"
)

$ErrorActionPreference = "Stop"

# Load bounds (safety rule 4), checked before anything else happens.
if ($Threads -lt 1 -or $Threads -gt 4) { throw "Threads must be within 1..4 (got $Threads)" }
if ($IoSize -lt 4096 -or $IoSize -gt 1048576 -or ($IoSize % 4096) -ne 0) {
    throw "IoSize must be a multiple of 4096 within 4096..1048576 (got $IoSize)"
}
if ($Seconds -lt 1 -or $Seconds -gt 300) { throw "Seconds must be within 1..300 (got $Seconds)" }
if ($ReadPercent -lt 0 -or $ReadPercent -gt 100) { throw "ReadPercent must be within 0..100" }

$source = @'
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace XcpNgTests
{
    public sealed class BlockHeader
    {
        public bool AllZero;
        public bool MagicOk;
        public bool CrcOk;
        public bool PayloadOk;
        public ulong RunId;
        public long Lba;
        public uint Gen;
        public long Wseq;
        public ulong Seed;
    }

    public sealed class BlockState
    {
        public long AckSeq = -1;
        public uint AckGen;
        public Dictionary<long, uint> Failed = new Dictionary<long, uint>();
    }

    public sealed class Classification
    {
        public string Class;
        public string Detail;
        public bool Problem;
    }

    public static class Blockstamp
    {
        public const int BlockSize = 4096;
        public const int HeaderSize = 64;
        public const int CrcOffset = 60;
        public const ulong Magic = 0x31504D54534B4C42UL; // "BLKSTMP1"
        public const int MaxThreads = 4;
        public const int MaxIoSize = 1 << 20;
        public const int MaxSeconds = 300;
        public const long ReservedBlocks = 256;
        public const int MaxProblemsListed = 4096;
        public const long SeqReservation = 1L << 24;

        const uint GENERIC_READ = 0x80000000;
        const uint GENERIC_WRITE = 0x40000000;
        const uint FILE_SHARE_READ = 1;
        const uint FILE_SHARE_WRITE = 2;
        const uint OPEN_EXISTING = 3;
        const uint FILE_FLAG_NO_BUFFERING = 0x20000000;
        const uint FILE_FLAG_WRITE_THROUGH = 0x80000000;
        const uint MEM_COMMIT = 0x1000;
        const uint MEM_RESERVE = 0x2000;
        const uint MEM_RELEASE = 0x8000;
        const uint PAGE_READWRITE = 0x04;
        const uint IOCTL_STORAGE_RESET_DEVICE = 0x002D5004;

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr sa,
            uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern IntPtr VirtualAlloc(IntPtr addr, UIntPtr size, uint type, uint protect);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool VirtualFree(IntPtr addr, UIntPtr size, uint freeType);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool SetFilePointerEx(SafeFileHandle h, long dist, out long newPos, uint method);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool ReadFile(SafeFileHandle h, IntPtr buf, uint n, out uint done, IntPtr ovl);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool WriteFile(SafeFileHandle h, IntPtr buf, uint n, out uint done, IntPtr ovl);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool FlushFileBuffers(SafeFileHandle h);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool DeviceIoControl(SafeFileHandle h, uint code, IntPtr inBuf, uint inSize,
            IntPtr outBuf, uint outSize, out uint returned, IntPtr ovl);

        // ---------------------------------------------------------------- CRC32 / PRNG / ids

        static readonly uint[] CrcTable = MakeCrcTable();

        static uint[] MakeCrcTable()
        {
            uint[] t = new uint[256];
            for (uint i = 0; i < 256; i++) {
                uint c = i;
                for (int k = 0; k < 8; k++)
                    c = ((c & 1) != 0) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
                t[i] = c;
            }
            return t;
        }

        public static uint Crc32Update(uint crc, byte[] buf, int offset, int count)
        {
            uint c = crc ^ 0xFFFFFFFFu;
            for (int i = offset; i < offset + count; i++)
                c = CrcTable[(c ^ buf[i]) & 0xFF] ^ (c >> 8);
            return c ^ 0xFFFFFFFFu;
        }

        // Same value as zlib crc32() over the bytes.
        public static uint Crc32(byte[] buf, int offset, int count)
        {
            return Crc32Update(0, buf, offset, count);
        }

        static ulong SplitMix(ref ulong x)
        {
            x += 0x9E3779B97F4A7C15UL;
            ulong z = x;
            z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9UL;
            z = (z ^ (z >> 27)) * 0x94D049BB133111EBUL;
            return z ^ (z >> 31);
        }

        static ulong Mix(ulong v)
        {
            ulong s = v;
            return SplitMix(ref s);
        }

        public static ulong RunIdOf(string run)
        {
            ulong h = 0xCBF29CE484222325UL;
            foreach (byte b in Encoding.UTF8.GetBytes(run)) {
                h ^= b;
                h *= 0x100000001B3UL;
            }
            return h;
        }

        public static ulong SeedOf(ulong runId, long lba, uint gen, long wseq)
        {
            return Mix(runId ^ Mix((ulong)lba) ^ Mix(((ulong)gen << 32) | 0x5EEDu) ^ Mix((ulong)wseq + 1));
        }

        static void PutU32(byte[] b, int o, uint v)
        {
            b[o] = (byte)v; b[o + 1] = (byte)(v >> 8); b[o + 2] = (byte)(v >> 16); b[o + 3] = (byte)(v >> 24);
        }

        static void PutU64(byte[] b, int o, ulong v)
        {
            for (int i = 0; i < 8; i++)
                b[o + i] = (byte)(v >> (8 * i));
        }

        static uint GetU32(byte[] b, int o)
        {
            return (uint)b[o] | ((uint)b[o + 1] << 8) | ((uint)b[o + 2] << 16) | ((uint)b[o + 3] << 24);
        }

        static ulong GetU64(byte[] b, int o)
        {
            ulong v = 0;
            for (int i = 7; i >= 0; i--)
                v = (v << 8) | b[o + i];
            return v;
        }

        static void FillPayload(byte[] b, int o, ulong seed)
        {
            ulong s = seed;
            for (int i = HeaderSize; i < BlockSize; i += 8)
                PutU64(b, o + i, SplitMix(ref s));
        }

        static uint BlockCrc(byte[] b, int o)
        {
            uint c = Crc32Update(0, b, o, CrcOffset);
            return Crc32Update(c, b, o + HeaderSize, BlockSize - HeaderSize);
        }

        // ---------------------------------------------------------------- block format

        public static void EncodeBlock(byte[] b, int o, ulong runId, long lba, uint gen, long wseq)
        {
            ulong seed = SeedOf(runId, lba, gen, wseq);
            Array.Clear(b, o, HeaderSize);
            PutU64(b, o + 0, Magic);
            PutU64(b, o + 8, runId);
            PutU64(b, o + 16, (ulong)lba);
            PutU32(b, o + 24, gen);
            PutU32(b, o + 28, 0);
            PutU64(b, o + 32, (ulong)wseq);
            PutU64(b, o + 40, seed);
            FillPayload(b, o, seed);
            PutU32(b, o + CrcOffset, BlockCrc(b, o));
        }

        public static BlockHeader DecodeBlock(byte[] b, int o)
        {
            BlockHeader h = new BlockHeader();
            h.AllZero = true;
            for (int i = 0; i < BlockSize; i++) {
                if (b[o + i] != 0) { h.AllZero = false; break; }
            }
            if (h.AllZero)
                return h;
            h.MagicOk = GetU64(b, o) == Magic;
            h.RunId = GetU64(b, o + 8);
            h.Lba = (long)GetU64(b, o + 16);
            h.Gen = GetU32(b, o + 24);
            h.Wseq = (long)GetU64(b, o + 32);
            h.Seed = GetU64(b, o + 40);
            h.CrcOk = GetU32(b, o + CrcOffset) == BlockCrc(b, o);
            if (h.MagicOk && h.CrcOk) {
                byte[] expect = new byte[BlockSize];
                FillPayload(expect, 0, h.Seed);
                h.PayloadOk = true;
                for (int i = HeaderSize; i < BlockSize; i++) {
                    if (expect[i] != b[o + i]) { h.PayloadOk = false; break; }
                }
            }
            return h;
        }

        // Classification of one block read back at actualLba, given the ledger state of that block.
        public static Classification Classify(BlockHeader h, long actualLba, ulong runId, BlockState st)
        {
            Classification c = new Classification();
            bool acked = st != null && st.AckSeq >= 0;
            if (h.AllZero) {
                c.Class = "UNWRITTEN";
                c.Problem = acked;
                c.Detail = acked ? "acked-write-missing" : "";
                return c;
            }
            if (!h.MagicOk) {
                c.Class = "CRC"; c.Problem = true; c.Detail = "bad-magic"; return c;
            }
            if (!h.CrcOk || !h.PayloadOk) {
                c.Class = "CRC"; c.Problem = true; c.Detail = h.CrcOk ? "payload" : "crc"; return c;
            }
            if (h.Lba != actualLba) {
                c.Class = "MISDIRECTED"; c.Problem = true;
                c.Detail = "hdr-lba=" + h.Lba.ToString(CultureInfo.InvariantCulture);
                return c;
            }
            if (h.RunId != runId) {
                c.Class = "MISDIRECTED"; c.Problem = true; c.Detail = "foreign-run"; return c;
            }
            if (acked && h.Wseq == st.AckSeq) {
                c.Class = "OK"; c.Detail = ""; c.Problem = false; return c;
            }
            if (acked && h.Wseq < st.AckSeq) {
                c.Class = "STALE"; c.Problem = true;
                c.Detail = "hdr-gen=" + h.Gen.ToString(CultureInfo.InvariantCulture)
                    + " acked-gen=" + st.AckGen.ToString(CultureInfo.InvariantCulture);
                return c;
            }
            // Newer than the last acknowledged write (or nothing acknowledged at all).
            c.Class = "FUTURE";
            if (st != null && st.Failed.ContainsKey(h.Wseq)) {
                c.Problem = false; c.Detail = "failed-write-landed";
            } else {
                c.Problem = true; c.Detail = "unledgered";
            }
            return c;
        }

        // ---------------------------------------------------------------- ledger

        public static string LedgerLine(long seq, string op, long lba, long count, uint gen, string status, int err)
        {
            return string.Format(CultureInfo.InvariantCulture, "{0},{1},{2},{3},{4},{5},{6},{7}",
                seq, op, lba, count, gen, status, err,
                DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture));
        }

        // Builds per-block state from ledger lines. Unparsable lines (e.g. a partial last line) are ignored.
        public static Dictionary<long, BlockState> StateFromLedger(IEnumerable<string> lines)
        {
            Dictionary<long, BlockState> state = new Dictionary<long, BlockState>();
            foreach (string line in lines) {
                string[] f = line.Split(',');
                if (f.Length < 8 || f[1] != "W")
                    continue;
                long seq, lba, count; uint gen;
                if (!long.TryParse(f[0], NumberStyles.Integer, CultureInfo.InvariantCulture, out seq)
                    || !long.TryParse(f[2], NumberStyles.Integer, CultureInfo.InvariantCulture, out lba)
                    || !long.TryParse(f[3], NumberStyles.Integer, CultureInfo.InvariantCulture, out count)
                    || !uint.TryParse(f[4], NumberStyles.Integer, CultureInfo.InvariantCulture, out gen))
                    continue;
                for (long b = lba; b < lba + count; b++) {
                    BlockState st;
                    if (!state.TryGetValue(b, out st)) {
                        st = new BlockState();
                        state[b] = st;
                    }
                    if (f[5] == "OK") {
                        if (seq > st.AckSeq) { st.AckSeq = seq; st.AckGen = gen; }
                    } else {
                        st.Failed[seq] = gen;
                    }
                }
            }
            return state;
        }

        sealed class Ledger : IDisposable
        {
            readonly FileStream fs;
            readonly object sync = new object();
            readonly bool durable;

            public Ledger(string path, bool durable)
            {
                this.durable = durable;
                fs = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete);
            }

            public void Append(string line)
            {
                byte[] b = Encoding.ASCII.GetBytes(line + "\n");
                lock (sync) {
                    fs.Write(b, 0, b.Length);
                    fs.Flush(durable);
                }
            }

            public void Dispose()
            {
                lock (sync) { fs.Flush(true); fs.Dispose(); }
            }
        }

        static IEnumerable<string> ReadLedger(string path)
        {
            if (!File.Exists(path))
                return new string[0];
            using (FileStream fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (StreamReader r = new StreamReader(fs, Encoding.ASCII)) {
                List<string> lines = new List<string>();
                string l;
                while ((l = r.ReadLine()) != null)
                    lines.Add(l);
                return lines;
            }
        }

        // Reserves a range of write sequence numbers that is unique across concurrent processes of this run.
        static long ReserveSeqBase(string dir, string run)
        {
            using (Mutex m = new Mutex(false, "Global\\blockstamp-" + run)) {
                try { m.WaitOne(); } catch (AbandonedMutexException) { }
                try {
                    string path = Path.Combine(dir, run + ".seq");
                    long next = SeqReservation;
                    if (File.Exists(path)) {
                        long v;
                        if (long.TryParse(File.ReadAllText(path).Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out v))
                            next = v;
                    }
                    File.WriteAllText(path, (next + SeqReservation).ToString(CultureInfo.InvariantCulture));
                    return next;
                } finally {
                    m.ReleaseMutex();
                }
            }
        }

        // ---------------------------------------------------------------- disk access

        sealed class AlignedBuffer : IDisposable
        {
            public IntPtr Ptr;
            public int Size;

            public AlignedBuffer(int size)
            {
                Size = size;
                Ptr = VirtualAlloc(IntPtr.Zero, (UIntPtr)(uint)size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
                if (Ptr == IntPtr.Zero)
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "VirtualAlloc");
            }

            public void Dispose()
            {
                if (Ptr != IntPtr.Zero) {
                    VirtualFree(Ptr, UIntPtr.Zero, MEM_RELEASE);
                    Ptr = IntPtr.Zero;
                }
            }
        }

        static SafeFileHandle OpenDisk(int disk)
        {
            SafeFileHandle h = CreateFileW("\\\\.\\PhysicalDrive" + disk.ToString(CultureInfo.InvariantCulture),
                GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, IntPtr.Zero, OPEN_EXISTING,
                FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH, IntPtr.Zero);
            if (h.IsInvalid)
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(), "CreateFile PhysicalDrive" + disk);
            return h;
        }

        // Returns 0 on success, else the Win32 error (or -1 for a short transfer).
        static int DoIo(SafeFileHandle h, bool write, long lba, AlignedBuffer buf, int bytes)
        {
            long pos;
            if (!SetFilePointerEx(h, lba * BlockSize, out pos, 0))
                return Marshal.GetLastWin32Error();
            uint done;
            bool ok = write
                ? WriteFile(h, buf.Ptr, (uint)bytes, out done, IntPtr.Zero)
                : ReadFile(h, buf.Ptr, (uint)bytes, out done, IntPtr.Zero);
            if (!ok)
                return Marshal.GetLastWin32Error();
            return done == (uint)bytes ? 0 : -1;
        }

        public static void CheckRange(long start, long count, long diskBytes)
        {
            if (count <= 0)
                throw new ArgumentException("Count must be > 0");
            if (start < ReservedBlocks)
                throw new ArgumentException("the first " + ReservedBlocks + " blocks are reserved");
            if (diskBytes > 0 && (start + count) * BlockSize > diskBytes)
                throw new ArgumentException("range exceeds the disk size");
        }

        public static void CheckLoad(int threads, int ioSize, int seconds)
        {
            if (threads < 1 || threads > MaxThreads)
                throw new ArgumentException("threads must be within 1.." + MaxThreads);
            if (ioSize < BlockSize || ioSize > MaxIoSize || ioSize % BlockSize != 0)
                throw new ArgumentException("ioSize must be a multiple of 4096 within 4096.." + MaxIoSize);
            if (seconds < 1 || seconds > MaxSeconds)
                throw new ArgumentException("seconds must be within 1.." + MaxSeconds);
        }

        static Dictionary<string, object> NewResult(string mode, string run, int disk)
        {
            Dictionary<string, object> r = new Dictionary<string, object>();
            r["mode"] = mode;
            r["run"] = run;
            r["runId"] = RunIdOf(run).ToString("x16");
            r["disk"] = disk;
            r["blockSize"] = BlockSize;
            r["startTime"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);
            return r;
        }

        static Dictionary<string, object> HeaderInfo(BlockHeader h)
        {
            Dictionary<string, object> d = new Dictionary<string, object>();
            if (h.AllZero) { d["zero"] = true; return d; }
            d["magicOk"] = h.MagicOk;
            d["crcOk"] = h.CrcOk;
            d["lba"] = h.Lba;
            d["gen"] = h.Gen;
            d["wseq"] = h.Wseq;
            d["runId"] = h.RunId.ToString("x16");
            return d;
        }

        static Dictionary<string, object> ProblemEntry(long lba, Classification c, BlockHeader h, BlockState st, string source)
        {
            Dictionary<string, object> p = new Dictionary<string, object>();
            p["lba"] = lba;
            p["class"] = c.Class;
            p["detail"] = c.Detail;
            p["problem"] = c.Problem;
            p["source"] = source;
            p["hdr"] = HeaderInfo(h);
            p["ackSeq"] = st == null ? -1 : st.AckSeq;
            p["ackGen"] = st == null ? 0 : (long)st.AckGen;
            p["t"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);
            return p;
        }

        // ---------------------------------------------------------------- modes

        public static Dictionary<string, object> Fill(int disk, long diskBytes, string dir, string run, uint gen,
            long start, long count, int ioSize)
        {
            CheckRange(start, count, diskBytes);
            CheckLoad(1, ioSize, 1);
            ulong runId = RunIdOf(run);
            long seq = ReserveSeqBase(dir, run);
            Dictionary<string, object> r = NewResult("fill", run, disk);
            int perIo = ioSize / BlockSize;
            long okBlocks = 0, errBlocks = 0;
            List<object> errors = new List<object>();
            byte[] tmp = new byte[ioSize];
            using (Ledger ledger = new Ledger(Path.Combine(dir, run + ".ledger"), false))
            using (SafeFileHandle h = OpenDisk(disk))
            using (AlignedBuffer buf = new AlignedBuffer(ioSize)) {
                for (long lba = start; lba < start + count; lba += perIo) {
                    int n = (int)Math.Min(perIo, start + count - lba);
                    seq++;
                    for (int i = 0; i < n; i++)
                        EncodeBlock(tmp, i * BlockSize, runId, lba + i, gen, seq);
                    Marshal.Copy(tmp, 0, buf.Ptr, n * BlockSize);
                    int err = DoIo(h, true, lba, buf, n * BlockSize);
                    ledger.Append(LedgerLine(seq, "W", lba, n, gen, err == 0 ? "OK" : "ERR", err));
                    if (err == 0) okBlocks += n;
                    else {
                        errBlocks += n;
                        if (errors.Count < 64) errors.Add(string.Format(CultureInfo.InvariantCulture, "lba={0} n={1} err={2}", lba, n, err));
                    }
                }
            }
            r["gen"] = gen; r["start"] = start; r["count"] = count;
            r["okBlocks"] = okBlocks; r["errBlocks"] = errBlocks; r["errors"] = errors;
            r["ok"] = errBlocks == 0;
            r["endTime"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);
            return r;
        }

        public static Dictionary<string, object> WriteSync(int disk, long diskBytes, string dir, string run, uint gen,
            long lba, long count, bool flush)
        {
            CheckRange(lba, count, diskBytes);
            if (count * BlockSize > MaxIoSize)
                throw new ArgumentException("write-sync is limited to 1 MiB");
            ulong runId = RunIdOf(run);
            long seq = ReserveSeqBase(dir, run) + 1;
            int bytes = (int)count * BlockSize;
            byte[] tmp = new byte[bytes];
            List<object> crcs = new List<object>();
            for (int i = 0; i < count; i++) {
                EncodeBlock(tmp, i * BlockSize, runId, lba + i, gen, seq);
                crcs.Add(Crc32(tmp, i * BlockSize, BlockSize).ToString("x8"));
            }
            Dictionary<string, object> r = NewResult("write-sync", run, disk);
            Stopwatch sw = new Stopwatch();
            int err, flushErr = 0;
            using (Ledger ledger = new Ledger(Path.Combine(dir, run + ".ledger"), true))
            using (SafeFileHandle h = OpenDisk(disk))
            using (AlignedBuffer buf = new AlignedBuffer(bytes)) {
                Marshal.Copy(tmp, 0, buf.Ptr, bytes);
                sw.Start();
                err = DoIo(h, true, lba, buf, bytes);
                if (err == 0 && flush) {
                    if (!FlushFileBuffers(h))
                        flushErr = Marshal.GetLastWin32Error();
                }
                sw.Stop();
                // A write whose flush failed is not considered acknowledged.
                bool acked = err == 0 && flushErr == 0;
                ledger.Append(LedgerLine(seq, "W", lba, count, gen, acked ? "OK" : "ERR", err != 0 ? err : flushErr));
            }
            r["gen"] = gen; r["lba"] = lba; r["count"] = count; r["seq"] = seq;
            r["status"] = (err == 0 && flushErr == 0) ? "OK" : "ERR";
            r["win32err"] = err;
            r["flushed"] = flush;
            r["flushErr"] = flushErr;
            r["durationMs"] = sw.ElapsedMilliseconds;
            r["blockCrcs"] = crcs;
            r["ok"] = err == 0 && flushErr == 0;
            r["endTime"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);
            return r;
        }

        public static Dictionary<string, object> Verify(int disk, long diskBytes, string dir, string run,
            long start, long count, int ioSize)
        {
            CheckRange(start, count, diskBytes);
            CheckLoad(1, ioSize, 1);
            ulong runId = RunIdOf(run);
            Dictionary<long, BlockState> state = StateFromLedger(ReadLedger(Path.Combine(dir, run + ".ledger")));
            Dictionary<string, object> r = NewResult("verify", run, disk);
            Dictionary<string, object> counts = new Dictionary<string, object>();
            foreach (string k in new string[] { "OK", "STALE", "MISDIRECTED", "CRC", "FUTURE", "UNWRITTEN" })
                counts[k] = 0L;
            List<object> problems = new List<object>();
            long problemCount = 0, readErrors = 0;
            int perIo = ioSize / BlockSize;
            byte[] tmp = new byte[ioSize];
            using (SafeFileHandle h = OpenDisk(disk))
            using (AlignedBuffer buf = new AlignedBuffer(ioSize)) {
                for (long lba = start; lba < start + count; lba += perIo) {
                    int n = (int)Math.Min(perIo, start + count - lba);
                    int err = DoIo(h, false, lba, buf, n * BlockSize);
                    if (err != 0) {
                        readErrors++;
                        if (problems.Count < MaxProblemsListed) {
                            Dictionary<string, object> p = new Dictionary<string, object>();
                            p["lba"] = lba; p["class"] = "READ-ERROR"; p["detail"] = "win32err=" + err; p["count"] = n;
                            p["problem"] = true;
                            problems.Add(p);
                        }
                        continue;
                    }
                    Marshal.Copy(buf.Ptr, tmp, 0, n * BlockSize);
                    for (int i = 0; i < n; i++) {
                        BlockState st;
                        state.TryGetValue(lba + i, out st);
                        BlockHeader hd = DecodeBlock(tmp, i * BlockSize);
                        Classification c = Classify(hd, lba + i, runId, st);
                        counts[c.Class] = (long)counts[c.Class] + 1;
                        if (c.Problem || c.Class == "FUTURE") {
                            if (c.Problem) problemCount++;
                            if (problems.Count < MaxProblemsListed)
                                problems.Add(ProblemEntry(lba + i, c, hd, st, "verify"));
                        }
                    }
                }
            }
            r["start"] = start; r["count"] = count;
            r["counts"] = counts;
            r["problemCount"] = problemCount;
            r["readErrors"] = readErrors;
            r["problems"] = problems;
            r["ok"] = problemCount == 0 && readErrors == 0;
            r["endTime"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);
            return r;
        }

        sealed class LoadWorker
        {
            public int Disk;
            public string Dir;
            public ulong RunId;
            public uint Gen;
            public long From, To; // [From, To) disjoint per worker, so per-block ordering is well defined
            public int IoSize;
            public int ReadPercent;
            public DateTime Deadline;
            public Dictionary<long, BlockState> State;
            public Ledger Ledger;
            public long SeqBase;
            public int Index;
            public long Writes, WriteErrors, Reads, ReadErrors, ReadMismatches;
            public List<object> Mismatches = new List<object>();
            public Dictionary<int, long> ErrorCodes = new Dictionary<int, long>();
            public string Fatal;
            public bool Done;
            public static long SeqCounter;

            public void Run()
            {
                try {
                    Random rng = new Random(unchecked((int)(RunId ^ (ulong)Index * 7919UL ^ (ulong)Environment.TickCount)));
                    int perIo = IoSize / BlockSize;
                    byte[] tmp = new byte[IoSize];
                    using (SafeFileHandle h = OpenDisk(Disk))
                    using (AlignedBuffer buf = new AlignedBuffer(IoSize)) {
                        while (DateTime.UtcNow < Deadline) {
                            long span = To - From;
                            int n = (int)Math.Min(span, 1 + rng.Next(perIo));
                            long lba = From + (long)(rng.NextDouble() * (span - n + 1));
                            if (lba + n > To) lba = To - n;
                            bool write = rng.Next(100) >= ReadPercent;
                            if (write) {
                                long seq = SeqBase + Interlocked.Increment(ref SeqCounter);
                                for (int i = 0; i < n; i++)
                                    EncodeBlock(tmp, i * BlockSize, RunId, lba + i, Gen, seq);
                                Marshal.Copy(tmp, 0, buf.Ptr, n * BlockSize);
                                int err = DoIo(h, true, lba, buf, n * BlockSize);
                                Ledger.Append(LedgerLine(seq, "W", lba, n, Gen, err == 0 ? "OK" : "ERR", err));
                                Writes++;
                                for (int i = 0; i < n; i++) {
                                    BlockState st = State[lba + i];
                                    if (err == 0) { st.AckSeq = seq; st.AckGen = Gen; }
                                    else st.Failed[seq] = Gen;
                                }
                                if (err != 0) { WriteErrors++; CountErr(err); }
                            } else {
                                int err = DoIo(h, false, lba, buf, n * BlockSize);
                                Reads++;
                                if (err != 0) {
                                    ReadErrors++; CountErr(err);
                                    Ledger.Append(LedgerLine(0, "R", lba, n, Gen, "ERR", err));
                                    continue;
                                }
                                Marshal.Copy(buf.Ptr, tmp, 0, n * BlockSize);
                                int bad = 0;
                                for (int i = 0; i < n; i++) {
                                    BlockState st = State[lba + i];
                                    BlockHeader hd = DecodeBlock(tmp, i * BlockSize);
                                    Classification c = Classify(hd, lba + i, RunId, st);
                                    if (c.Problem) {
                                        bad++;
                                        if (Mismatches.Count < 1024)
                                            Mismatches.Add(ProblemEntry(lba + i, c, hd, st, "load-read"));
                                    }
                                }
                                if (bad > 0) ReadMismatches++;
                                Ledger.Append(LedgerLine(0, "R", lba, n, Gen, bad > 0 ? "MISMATCH" : "OK", 0));
                            }
                        }
                    }
                } catch (Exception e) {
                    Fatal = e.GetType().Name + ": " + e.Message;
                } finally {
                    Done = true;
                }
            }

            void CountErr(int err)
            {
                long v;
                ErrorCodes.TryGetValue(err, out v);
                ErrorCodes[err] = v + 1;
            }
        }

        public static Dictionary<string, object> Load(int disk, long diskBytes, string dir, string run, uint gen,
            long start, long count, int threads, int ioSize, int seconds, int readPercent)
        {
            CheckRange(start, count, diskBytes);
            CheckLoad(threads, ioSize, seconds);
            if (count < threads)
                throw new ArgumentException("range too small for the thread count");
            ulong runId = RunIdOf(run);
            string ledgerPath = Path.Combine(dir, run + ".ledger");
            Dictionary<long, BlockState> state = StateFromLedger(ReadLedger(ledgerPath));
            for (long b = start; b < start + count; b++) {
                if (!state.ContainsKey(b))
                    state[b] = new BlockState();
            }
            long seqBase = ReserveSeqBase(dir, run);
            LoadWorker.SeqCounter = 0;
            Dictionary<string, object> r = NewResult("load", run, disk);
            DateTime deadline = DateTime.UtcNow.AddSeconds(seconds);
            List<LoadWorker> workers = new List<LoadWorker>();
            List<Thread> ts = new List<Thread>();
            using (Ledger ledger = new Ledger(ledgerPath, false)) {
                long per = count / threads;
                for (int t = 0; t < threads; t++) {
                    LoadWorker w = new LoadWorker();
                    w.Disk = disk; w.Dir = dir; w.RunId = runId; w.Gen = gen; w.IoSize = ioSize;
                    w.ReadPercent = readPercent; w.Deadline = deadline; w.State = state; w.Ledger = ledger;
                    w.SeqBase = seqBase; w.Index = t;
                    w.From = start + t * per;
                    w.To = (t == threads - 1) ? start + count : w.From + per;
                    Thread th = new Thread(w.Run);
                    th.IsBackground = true;
                    workers.Add(w); ts.Add(th);
                    th.Start();
                }
                // Watchdog: a hung I/O must not keep us beyond the bound for long.
                DateTime hardStop = deadline.AddSeconds(120);
                foreach (Thread th in ts) {
                    TimeSpan left = hardStop - DateTime.UtcNow;
                    if (left < TimeSpan.Zero) left = TimeSpan.Zero;
                    th.Join(left);
                }
                long writes = 0, writeErrors = 0, reads = 0, readErrors = 0, mism = 0;
                List<object> mismatches = new List<object>();
                List<object> fatal = new List<object>();
                Dictionary<string, object> codes = new Dictionary<string, object>();
                int hung = 0;
                foreach (LoadWorker w in workers) {
                    if (!w.Done) hung++;
                    writes += w.Writes; writeErrors += w.WriteErrors; reads += w.Reads; readErrors += w.ReadErrors;
                    mism += w.ReadMismatches;
                    lock (w.Mismatches) { mismatches.AddRange(w.Mismatches); }
                    if (w.Fatal != null) fatal.Add(w.Fatal);
                    foreach (KeyValuePair<int, long> kv in w.ErrorCodes) {
                        string k = kv.Key.ToString(CultureInfo.InvariantCulture);
                        codes[k] = (codes.ContainsKey(k) ? (long)codes[k] : 0L) + kv.Value;
                    }
                }
                r["gen"] = gen; r["start"] = start; r["count"] = count; r["threads"] = threads;
                r["ioSize"] = ioSize; r["seconds"] = seconds; r["readPercent"] = readPercent;
                r["writes"] = writes; r["writeErrors"] = writeErrors;
                r["reads"] = reads; r["readErrors"] = readErrors;
                r["readMismatches"] = mism;
                r["mismatches"] = mismatches;
                r["errorCodes"] = codes;
                r["fatal"] = fatal;
                r["hungThreads"] = hung;
                r["ok"] = mism == 0 && fatal.Count == 0 && hung == 0;
                r["endTime"] = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture);
            }
            return r;
        }

        public static Dictionary<string, object> ResetDevice(int disk)
        {
            Dictionary<string, object> r = NewResult("reset-device", "", disk);
            Stopwatch sw = Stopwatch.StartNew();
            int err = 0;
            using (SafeFileHandle h = OpenDisk(disk)) {
                uint returned;
                if (!DeviceIoControl(h, IOCTL_STORAGE_RESET_DEVICE, IntPtr.Zero, 0, IntPtr.Zero, 0, out returned, IntPtr.Zero))
                    err = Marshal.GetLastWin32Error();
            }
            sw.Stop();
            r["win32err"] = err;
            r["durationMs"] = sw.ElapsedMilliseconds;
            r["ok"] = err == 0;
            return r;
        }

        // ---------------------------------------------------------------- self test (no disk access)

        static void Expect(bool cond, string what, List<object> checks)
        {
            if (!cond)
                throw new Exception("selftest failed: " + what);
            checks.Add(what);
        }

        public static Dictionary<string, object> SelfTest()
        {
            List<object> checks = new List<object>();
            byte[] vec = Encoding.ASCII.GetBytes("123456789");
            Expect(Crc32(vec, 0, vec.Length) == 0xCBF43926u, "crc32 check value", checks);

            ulong run = RunIdOf("selftest");
            Expect(run != RunIdOf("selftest2"), "run ids differ", checks);
            byte[] b = new byte[BlockSize * 2];
            EncodeBlock(b, BlockSize, run, 1000, 3, 42);
            BlockHeader h = DecodeBlock(b, BlockSize);
            Expect(!h.AllZero && h.MagicOk && h.CrcOk && h.PayloadOk, "decode valid block", checks);
            Expect(h.RunId == run && h.Lba == 1000 && h.Gen == 3 && h.Wseq == 42, "header fields", checks);
            Expect(h.Seed == SeedOf(run, 1000, 3, 42), "seed", checks);
            Expect(Encoding.ASCII.GetString(b, BlockSize, 8) == "BLKSTMP1", "magic bytes", checks);

            byte[] b2 = new byte[BlockSize];
            EncodeBlock(b2, 0, run, 1000, 3, 43);
            Expect(Crc32(b2, 0, BlockSize) != Crc32(b, BlockSize, BlockSize), "different wseq gives different block", checks);

            BlockState st = new BlockState();
            st.AckSeq = 42; st.AckGen = 3;
            Expect(Classify(h, 1000, run, st).Class == "OK", "classify OK", checks);
            Expect(!Classify(h, 1000, run, st).Problem, "OK is not a problem", checks);

            BlockState newer = new BlockState();
            newer.AckSeq = 50; newer.AckGen = 4;
            Classification c = Classify(h, 1000, run, newer);
            Expect(c.Class == "STALE" && c.Problem, "classify STALE", checks);

            c = Classify(h, 1001, run, st);
            Expect(c.Class == "MISDIRECTED" && c.Problem && c.Detail == "hdr-lba=1000", "classify MISDIRECTED", checks);
            c = Classify(h, 1000, RunIdOf("other"), st);
            Expect(c.Class == "MISDIRECTED" && c.Detail == "foreign-run", "classify foreign run", checks);

            byte[] torn = (byte[])b.Clone();
            torn[BlockSize + 2000] ^= 0x55;
            c = Classify(DecodeBlock(torn, BlockSize), 1000, run, st);
            Expect(c.Class == "CRC" && c.Problem, "classify CRC (payload)", checks);
            torn = (byte[])b.Clone();
            torn[BlockSize + 20] ^= 0x01;
            c = Classify(DecodeBlock(torn, BlockSize), 1000, run, st);
            Expect(c.Class == "CRC", "classify CRC (header)", checks);
            torn = (byte[])b.Clone();
            torn[BlockSize] ^= 0xFF;
            c = Classify(DecodeBlock(torn, BlockSize), 1000, run, st);
            Expect(c.Class == "CRC" && c.Detail == "bad-magic", "classify bad magic", checks);

            byte[] zero = new byte[BlockSize];
            c = Classify(DecodeBlock(zero, 0), 1000, run, null);
            Expect(c.Class == "UNWRITTEN" && !c.Problem, "classify UNWRITTEN", checks);
            c = Classify(DecodeBlock(zero, 0), 1000, run, st);
            Expect(c.Class == "UNWRITTEN" && c.Problem, "classify lost acknowledged write", checks);

            BlockState older = new BlockState();
            older.AckSeq = 40; older.AckGen = 2; older.Failed[42] = 3;
            c = Classify(h, 1000, run, older);
            Expect(c.Class == "FUTURE" && !c.Problem, "classify FUTURE (failed write landed)", checks);
            older.Failed.Clear();
            c = Classify(h, 1000, run, older);
            Expect(c.Class == "FUTURE" && c.Problem, "classify unledgered newer write", checks);

            List<string> lines = new List<string>();
            lines.Add(LedgerLine(40, "W", 998, 4, 2, "OK", 0));
            lines.Add(LedgerLine(42, "W", 1000, 1, 3, "ERR", 1117));
            lines.Add(LedgerLine(0, "R", 1000, 1, 3, "OK", 0));
            lines.Add("41,W,10"); // partial line
            Dictionary<long, BlockState> s = StateFromLedger(lines);
            Expect(s.ContainsKey(998) && s[1001].AckSeq == 40 && s[1001].AckGen == 2, "ledger acked state", checks);
            Expect(s[1000].Failed.ContainsKey(42), "ledger failed state", checks);
            Expect(Classify(h, 1000, run, s[1000]).Class == "FUTURE", "ledger-driven FUTURE", checks);

            bool threw = false;
            try { CheckLoad(5, 4096, 10); } catch (ArgumentException) { threw = true; }
            Expect(threw, "reject 5 threads", checks);
            threw = false;
            try { CheckLoad(1, 2 << 20, 10); } catch (ArgumentException) { threw = true; }
            Expect(threw, "reject 2 MiB I/O", checks);
            threw = false;
            try { CheckLoad(1, 4096, 301); } catch (ArgumentException) { threw = true; }
            Expect(threw, "reject 301 s", checks);
            threw = false;
            try { CheckRange(0, 8, 0); } catch (ArgumentException) { threw = true; }
            Expect(threw, "reject reserved blocks", checks);
            threw = false;
            try { CheckRange(1000, 8, 1000 * 4096L); } catch (ArgumentException) { threw = true; }
            Expect(threw, "reject range past end", checks);
            CheckLoad(4, 1 << 20, 300);
            checks.Add("accept maximum bounds");

            Dictionary<string, object> r = NewResult("selftest", "selftest", -1);
            r["checks"] = checks;
            r["ok"] = true;
            return r;
        }
    }
}
'@

if (-not ("XcpNgTests.Blockstamp" -as [type])) {
    Add-Type -TypeDefinition $source -Language CSharp
}

function Write-Result($Result) {
    $problems = @()
    if ($Result.ContainsKey("problems")) { $problems += $Result["problems"] }
    if ($Result.ContainsKey("mismatches")) { $problems += $Result["mismatches"] }
    $shown = 0
    foreach ($p in $problems) {
        if ($shown -ge 200) { Write-Output "PROBLEM ... (truncated, see JSON)"; break }
        $tag = if ($p["problem"]) { "PROBLEM" } else { "NOTE" }
        Write-Output ("{0} lba={1} class={2} detail={3}" -f $tag, $p["lba"], $p["class"], $p["detail"])
        $shown++
    }
    $json = ConvertTo-Json -InputObject $Result -Depth 8 -Compress
    if ($Out) {
        $tmp = "$Out.tmp"
        [System.IO.File]::WriteAllText($tmp, $json)
        Move-Item -Force -LiteralPath $tmp -Destination $Out
    }
    Write-Output "BLOCKSTAMP-RESULT $json"
}

if ($Mode -eq "selftest") {
    # No disk is touched in this mode.
    Write-Result ([XcpNgTests.Blockstamp]::SelfTest())
    exit 0
}

# ------------------------------------------------------------------ disk safety checks (before any handle)

if ($Disk -lt 0) { throw "-Disk is required" }
if ($Mode -ne "reset-device" -and $Run -notmatch '^[A-Za-z0-9_.-]{1,64}$') {
    throw "-Run must match ^[A-Za-z0-9_.-]{1,64}$"
}
$diskInfo = Get-Disk -Number $Disk
if ($diskInfo.IsSystem -or $diskInfo.IsBoot) { throw "refusing disk $Disk : system or boot disk" }
if ($diskInfo.PartitionStyle -ne "RAW") { throw "refusing disk $Disk : partition style $($diskInfo.PartitionStyle) is not RAW" }
$partitions = @(Get-Partition -DiskNumber $Disk -ErrorAction SilentlyContinue)
if ($partitions.Count -ne 0) { throw "refusing disk $Disk : it has $($partitions.Count) partition(s)" }
$systemDisk = (Get-Partition -DriveLetter ($env:SystemDrive[0]) | Get-Disk).Number
if ($systemDisk -eq $Disk) { throw "refusing disk $Disk : it hosts $env:SystemDrive" }
if ($ExpectSize -gt 0 -and $diskInfo.Size -ne $ExpectSize) {
    throw "refusing disk $Disk : size $($diskInfo.Size) differs from the expected $ExpectSize"
}
if ($diskInfo.IsOffline) { throw "refusing disk $Disk : offline" }
if ($diskInfo.IsReadOnly -and $Mode -in @("fill", "load", "write-sync")) { throw "refusing disk $Disk : read-only" }

New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$diskBytes = [long]$diskInfo.Size

switch ($Mode) {
    "fill" {
        $result = [XcpNgTests.Blockstamp]::Fill($Disk, $diskBytes, $Dir, $Run, [uint32]$Gen, $Start, $Count, $IoSize)
    }
    "load" {
        $result = [XcpNgTests.Blockstamp]::Load($Disk, $diskBytes, $Dir, $Run, [uint32]$Gen, $Start, $Count,
            $Threads, $IoSize, $Seconds, $ReadPercent)
    }
    "write-sync" {
        if ($Lba -lt 0) { throw "-Lba is required for write-sync" }
        $result = [XcpNgTests.Blockstamp]::WriteSync($Disk, $diskBytes, $Dir, $Run, [uint32]$Gen, $Lba, $Count,
            [bool]$Flush)
    }
    "verify" {
        $result = [XcpNgTests.Blockstamp]::Verify($Disk, $diskBytes, $Dir, $Run, $Start, $Count, $IoSize)
    }
    "reset-device" {
        $result = [XcpNgTests.Blockstamp]::ResetDevice($Disk)
    }
}
Write-Result $result
