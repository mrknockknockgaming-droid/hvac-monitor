using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Fullscope.Cloud.Data;
using Fullscope.Core;
using Microsoft.AspNetCore.Builder;

namespace Fullscope.Cloud.Api;

/// <summary>
/// Kits, boards and warranty (Fullscope Office), and the contractor's QR claim; port of
/// hvaccloud/kits.py. A kit is one shipped Fullscope: an outdoor and an indoor board flashed with the
/// same kit code (FS-7K3QX). The box label carries a QR code for {APP_URL}#/claim/&lt;kit code&gt;-&lt;claim code&gt;.
/// A contractor scans it, signs in and names the system: the server creates the system under their
/// account with site_id = the kit code in lower case (the boards' MQTT topics), starts the boards'
/// warranty and invites the homeowner. Staff list kits and boards, mark kits shipped to a customer,
/// open warranty claims and release a kit claimed by mistake.
/// </summary>
public sealed partial class ApiServer
{
    const string CodeAlphabet = "23456789ABCDEFGHJKMNPQRSTUVWXYZ";      // no 0/O, 1/I/L: read off a label without mistakes
    static readonly string[] ClaimStatuses = ["open", "replaced", "repaired", "denied"];
    readonly Throttle _claimThrottle = new(limit: 10, window: 15 * 60); // wrong claim codes per person

    [GeneratedRegex(@"^FS-[A-Z0-9]{3,10}$")]
    private static partial Regex KitCode();

    [GeneratedRegex(@"[\s:.-]")]
    private static partial Regex SerialSeparators();

    [GeneratedRegex(@"^[0-9A-F]{12}$")]
    private static partial Regex TwelveHex();

    [GeneratedRegex(@"\s")]
    private static partial Regex Space();

    void KitRoutes(WebApplication app)
    {
        Route(app, "GET", "/api/office/kits", ListKits);
        Route(app, "POST", "/api/office/kits", AddKit, body: true);
        Route(app, "GET", "/api/office/kits/{kit_id}", GetKit);
        Route(app, "PATCH", "/api/office/kits/{kit_id}", UpdateKit, body: true);
        Route(app, "POST", "/api/office/kits/{kit_id}/new-code", NewClaimCode);
        Route(app, "POST", "/api/office/kits/{kit_id}/release", ReleaseKit);
        Route(app, "GET", "/api/office/boards", ListBoards);
        Route(app, "POST", "/api/office/boards/{board_id}/claims", OpenWarrantyClaim, body: true);
        Route(app, "PATCH", "/api/office/claims/{claim_id}", SettleWarrantyClaim, body: true);
        Route(app, "POST", "/api/claim/lookup", ClaimLookup, body: true);
        Route(app, "POST", "/api/claim", ClaimKit, body: true);
    }

    /// <summary>The same day n months later, or the month's last day when it is shorter.</summary>
    public static DateOnly AddMonths(DateOnly d, int n) => d.AddMonths(n);

    /// <summary>A board serial (its MAC) as AA:BB:CC:DD:EE:FF, from any common spelling; null if it isn't one.</summary>
    public static string? SerialOf(string? raw)
    {
        var hexes = SerialSeparators().Replace(raw ?? "", "").ToUpperInvariant();
        if (!TwelveHex().IsMatch(hexes)) return null;
        return string.Join(":", Enumerable.Range(0, 6).Select(i => hexes.Substring(i * 2, 2)));
    }

    static string RandomCode(int n) => new(Enumerable.Range(0, n).Select(_ => CodeAlphabet[RandomNumberGenerator.GetInt32(CodeAlphabet.Length)]).ToArray());

    /// <summary>'fs-7k3qx-abcd2345' (the QR's end, or typed off the label) -> ("FS-7K3QX", "ABCD2345").</summary>
    static (string Kit, string Claim) SplitCode(string? raw)
    {
        var code = Space().Replace(raw ?? "", "").ToUpperInvariant();
        int i = code.LastIndexOf('-');
        return i < 0 ? ("", code) : (code[..i], code[(i + 1)..]);
    }

    JsonObject WarrantyView(Board b)
    {
        if (b.WarrantyStart is not DateOnly start)
            return new JsonObject { ["start"] = null, ["months"] = b.WarrantyMonths, ["ends"] = null, ["active"] = null };
        var ends = AddMonths(start, b.WarrantyMonths is int m && m != 0 ? m : _settings.WarrantyMonths);
        return new JsonObject
        {
            ["start"] = Service.IsoDate(start), ["months"] = b.WarrantyMonths, ["ends"] = Service.IsoDate(ends), ["active"] = Time.Today() <= ends,
        };
    }

    static string KitStatus(Kit k) => k.ClaimedAt is not null ? "installed" : k.ShippedAt is not null ? "shipped" : "stock";

    string ClaimUrl(Kit k) => $"{_settings.AppUrl}#/claim/{k.Code}-{k.ClaimCode}";

    static List<Board> KitBoards(Db s, int kitId) =>
        s.Boards.Where(b => b.KitId == kitId).OrderByDescending(b => b.Kind).ThenBy(b => b.Id).ToList();

    JsonObject BoardView(Db s, Board b, bool live = true)
    {
        var o = new JsonObject
        {
            ["id"] = b.Id, ["serial"] = b.Serial, ["kind"] = b.Kind, ["hw_rev"] = b.HwRev, ["fw"] = b.Fw, ["status"] = b.Status,
            ["notes"] = b.Notes, ["created_at"] = Iso(b.CreatedAt), ["warranty"] = WarrantyView(b), ["kit_id"] = b.KitId,
        };
        if (live)                                                   // the node as the cloud last heard from it
        {
            var d = s.Devices.Where(x => x.Mac == b.Serial).OrderByDescending(x => x.LastSeen).FirstOrDefault();
            o["seen"] = d is null ? null : new JsonObject { ["fw"] = d.Fw, ["last_seen"] = Iso(d.LastSeen), ["rssi"] = d.Rssi };
        }
        return o;
    }

    static JsonObject WarrantyClaimView(Db s, WarrantyClaim c)
    {
        var board = s.Boards.Find(c.BoardId)!;
        var repl = c.ReplacementId is int rid ? s.Boards.Find(rid) : null;
        return new JsonObject
        {
            ["id"] = c.Id, ["board"] = new JsonObject { ["id"] = board.Id, ["serial"] = board.Serial, ["kind"] = board.Kind },
            ["opened_at"] = Iso(c.OpenedAt), ["problem"] = c.Problem, ["status"] = c.Status, ["resolution"] = c.Resolution,
            ["closed_at"] = Iso(c.ClosedAt), ["replacement"] = repl is null ? null : new JsonObject { ["id"] = repl.Id, ["serial"] = repl.Serial },
        };
    }

    static JsonObject KitRow(Db s, Kit k)
    {
        var boards = KitBoards(s, k.Id);
        var cust = k.CustomerId is int cid ? s.Customers.Find(cid) : null;
        var system = k.SystemId is int sid ? s.Systems.Find(sid) : null;
        return new JsonObject
        {
            ["id"] = k.Id, ["code"] = k.Code, ["status"] = KitStatus(k), ["hw_rev"] = k.HwRev, ["created_at"] = Iso(k.CreatedAt),
            ["shipped_at"] = Iso(k.ShippedAt), ["claimed_at"] = Iso(k.ClaimedAt),
            ["customer"] = cust is null ? null : new JsonObject { ["id"] = cust.Id, ["company"] = cust.Company },
            ["system"] = system is null ? null : new JsonObject { ["id"] = system.Id, ["name"] = system.Name, ["site_id"] = system.SiteId, ["address"] = system.Address },
            ["boards"] = new JsonArray(boards.Select(b => (JsonNode?)new JsonObject { ["id"] = b.Id, ["kind"] = b.Kind, ["serial"] = b.Serial }).ToArray()),
        };
    }

    JsonObject KitDetail(Db s, Kit k)
    {
        var boards = KitBoards(s, k.Id);
        var ids = boards.Select(b => b.Id).ToList();
        var claims = ids.Count == 0 ? [] :
            s.WarrantyClaims.Where(c => ids.Contains(c.BoardId) || (c.ReplacementId != null && ids.Contains(c.ReplacementId.Value)))
             .OrderByDescending(c => c.Id).ToList();
        var by = k.ClaimedBy is int uid ? s.Users.Find(uid) : null;
        var o = KitRow(s, k);
        o["notes"] = k.Notes;
        o["claim_url"] = ClaimUrl(k);
        o["site_id"] = k.Code.ToLowerInvariant();
        o["claimed_by"] = by is null ? null : by.Name is { Length: > 0 } nm ? nm : by.Email;
        o["boards"] = new JsonArray(boards.Select(b => (JsonNode?)BoardView(s, b)).ToArray());
        o["claims"] = new JsonArray(claims.Select(c => (JsonNode?)WarrantyClaimView(s, c)).ToArray());
        return o;
    }

    static Kit FindKit(Req r) =>
        r.S.Kits.Find(Validate.PathInt("kit_id", r.Route("kit_id"))) ?? throw new ApiError(404, "no such kit");

    // ---------- staff ----------

    /// <summary>Every kit, newest first.</summary>
    object? ListKits(Req r)
    {
        StaffOnly(r.Who);
        var rows = r.S.Kits.OrderByDescending(k => k.CreatedAt).ThenByDescending(k => k.Id).ToList();
        return new JsonArray(rows.Select(k => (JsonNode?)KitRow(r.S, k)).ToArray());
    }

    static readonly Field[] BoardFields = [Field.Choice("kind", ["outdoor", "indoor"], required: true), Field.Str("serial", 20, required: true), Field.Str("fw", 32)];

    /// <summary>Register a kit and its boards (by serial). The code is made up unless given.</summary>
    object? AddKit(Req r)
    {
        StaffOnly(r.Who);
        var b = Body(r, Field.Str("code", 16), Field.Str("hw_rev", 16), Field.Str("notes", 1000),
                     new Field("boards", FieldType.ListOfDict) { Required = true, Nullable = false });
        var items = b.Node("boards")!.AsArray();
        var boards = new List<Model>();
        var errors = new List<JsonObject>();
        if (items.Count < 1) errors.Add(ApiError.Problem("too_short", "List should have at least 1 item after validation, not 0", items, "body", "boards"));
        else if (items.Count > 4) errors.Add(ApiError.Problem("too_long", $"List should have at most 4 items after validation, not {items.Count}", items, "body", "boards"));
        else
            for (int i = 0; i < items.Count; i++)
            {
                try { boards.Add(Validate.Body(items[i], true, BoardFields)); }
                catch (ApiError e) when (e.Detail is JsonArray list)
                {
                    foreach (var p in list.OfType<JsonObject>())                // body.x -> body.boards.i.x
                    {
                        var loc = p["loc"]!.AsArray().Select(x => x!.DeepClone()).ToList();
                        p["loc"] = new JsonArray([JsonValue.Create("body"), JsonValue.Create("boards"), JsonValue.Create(i), .. loc.Skip(1)]);
                        errors.Add((JsonObject)p.DeepClone());
                    }
                }
            }
        if (errors.Count > 0) throw ApiError.Invalid([.. errors]);

        string code;
        if (b.Str("code") is string given)
        {
            code = given.Trim().ToUpperInvariant();
            if (!KitCode().IsMatch(code)) throw new ApiError(422, "A kit code looks like FS-7K3QX.");
        }
        else
        {
            code = "FS-" + RandomCode(5);
            while (r.S.Kits.Any(k => k.Code == code)) code = "FS-" + RandomCode(5);
        }
        if (r.S.Kits.Any(k => k.Code == code)) throw new ApiError(409, $"There is already a kit {code}.");
        var site = code.ToLowerInvariant();
        if (r.S.Systems.Any(x => x.SiteId == site)) throw new ApiError(409, $"A system already reports as {site}.");
        var kinds = boards.Select(x => x.Str("kind")!).ToList();
        if (kinds.Distinct().Count() != kinds.Count) throw new ApiError(422, "A kit has one board of each kind.");
        var rows = new List<(Board? Board, string Serial, Model In)>();
        foreach (var bi in boards)
        {
            var serial = SerialOf(bi.Str("serial"))
                ?? throw new ApiError(422, $"{bi.Str("serial")!.Trim()} isn't a board serial (12 hex digits, like 24:6F:28:AB:CD:EF).");
            var board = r.S.Boards.FirstOrDefault(x => x.Serial == serial);
            if (board is not null && board.KitId is not null) throw new ApiError(409, $"Board {serial} is already in another kit.");
            rows.Add((board, serial, bi));
        }
        var kit = new Kit { Code = code, ClaimCode = RandomCode(8), HwRev = Clean(b.Str("hw_rev")), Notes = Clean(b.Str("notes")) };
        r.S.Kits.Add(kit);
        r.S.SaveChanges();
        foreach (var (existing, serial, bi) in rows)
        {
            var board = existing;
            if (board is null)
            {
                board = new Board { Serial = serial, Kind = bi.Str("kind")! };
                r.S.Boards.Add(board);
            }
            board.Kind = bi.Str("kind")!;
            board.KitId = kit.Id;
            board.Status = "stock";
            board.HwRev = kit.HwRev ?? board.HwRev;
            board.Fw = Clean(bi.Str("fw")) ?? board.Fw;
        }
        r.S.SaveChanges();
        return KitDetail(r.S, kit);
    }

    object? GetKit(Req r)
    {
        StaffOnly(r.Who);
        return KitDetail(r.S, FindKit(r));
    }

    /// <summary>Ship it to a customer (or take that back), and notes.</summary>
    object? UpdateKit(Req r)
    {
        StaffOnly(r.Who);
        var b = Body(r, Field.Int("customer_id", ge: 0), Field.Bool("shipped"), Field.Str("notes", 1000));
        var k = FindKit(r);
        if (b.IsSet("customer_id"))
        {
            if (b.Int("customer_id") is int cid && cid != 0 && r.S.Customers.Find(cid) is null) throw new ApiError(404, "no such customer");
            k.CustomerId = b.Int("customer_id") is int c && c != 0 ? c : null;
        }
        if (b.IsSet("notes")) k.Notes = Clean(b.Str("notes"));
        if (b.Bool("shipped") is bool shipped && k.ClaimedAt is null)
        {
            k.ShippedAt = shipped ? k.ShippedAt ?? Db.UtcNow() : null;
            foreach (var board in r.S.Boards.Where(x => x.KitId == k.Id && (x.Status == "stock" || x.Status == "shipped")).ToList())
                board.Status = shipped ? "shipped" : "stock";
        }
        r.S.SaveChanges();
        return KitDetail(r.S, k);
    }

    /// <summary>A new claim code (print a new label): the old QR stops working.</summary>
    object? NewClaimCode(Req r)
    {
        StaffOnly(r.Who);
        var k = FindKit(r);
        if (k.ClaimedAt is not null) throw new ApiError(409, "This kit is already claimed.");
        k.ClaimCode = RandomCode(8);
        r.S.SaveChanges();
        return KitDetail(r.S, k);
    }

    /// <summary>Undo a claim made by mistake. The system it made stays with that contractor, renamed out of the
    /// kit's way (its site_id gets -old&lt;id&gt;), so the kit can be claimed again.</summary>
    object? ReleaseKit(Req r)
    {
        StaffOnly(r.Who);
        var k = FindKit(r);
        if (k.ClaimedAt is null) throw new ApiError(409, "This kit isn't claimed.");
        var system = k.SystemId is int sid ? r.S.Systems.Find(sid) : null;
        if (system is not null && system.SiteId == k.Code.ToLowerInvariant()) system.SiteId = $"{system.SiteId}-old{system.Id}";
        k.ClaimedAt = null;
        k.ClaimedBy = k.AccountId = k.SystemId = null;
        k.ClaimCode = RandomCode(8);
        foreach (var board in r.S.Boards.Where(x => x.KitId == k.Id && x.Status == "installed").ToList())
        {
            board.Status = k.ShippedAt is not null ? "shipped" : "stock";
            board.WarrantyStart = null;
        }
        r.S.SaveChanges();
        return KitDetail(r.S, k);
    }

    /// <summary>Boards whose serial contains q (any spelling), newest first; at most 100.</summary>
    object? ListBoards(Req r)
    {
        StaffOnly(r.Who);
        var q = Validate.Query(r.Query("q"), Field.Str("q", 20), JsonValue.Create("")).GetValue<string>();
        var hexes = SerialSeparators().Replace(q, "").ToUpperInvariant();
        var rows = r.S.Boards.OrderByDescending(x => x.CreatedAt).ThenByDescending(x => x.Id).ToList()
                    .Where(x => x.Serial.Replace(":", "").Contains(hexes, StringComparison.Ordinal)).Take(100);
        return new JsonArray(rows.Select(x => (JsonNode?)BoardView(r.S, x, live: false)).ToArray());
    }

    /// <summary>Open a warranty claim on a board (it is marked RMA until the claim is settled).</summary>
    object? OpenWarrantyClaim(Req r)
    {
        StaffOnly(r.Who);
        var b = Body(r, Field.Str("problem", 2000, required: true, minLength: 1));
        var board = r.S.Boards.Find(Validate.PathInt("board_id", r.Route("board_id"))) ?? throw new ApiError(404, "no such board");
        var problem = b.Str("problem")!.Trim();
        if (problem.Length == 0) throw new ApiError(422, "Describe the problem.");
        var c = new WarrantyClaim { BoardId = board.Id, Problem = problem };
        r.S.WarrantyClaims.Add(c);
        board.Status = "rma";
        r.S.SaveChanges();
        return WarrantyClaimView(r.S, c);
    }

    /// <summary>Settle a warranty claim. "replaced": the replacement board takes the old one's place in the kit
    /// and keeps its warranty; the old board is retired.</summary>
    object? SettleWarrantyClaim(Req r)
    {
        StaffOnly(r.Who);
        var b = Body(r, Field.Choice("status", ClaimStatuses, required: true), Field.Str("resolution", 2000), Field.Str("replacement_serial", 20));
        var c = r.S.WarrantyClaims.Find(Validate.PathInt("claim_id", r.Route("claim_id"))) ?? throw new ApiError(404, "no such claim");
        var old = r.S.Boards.Find(c.BoardId)!;
        var status = b.Str("status")!;
        if (status == "replaced")
        {
            var serial = SerialOf(b.Str("replacement_serial") ?? "") ?? throw new ApiError(422, "Enter the replacement board's serial.");
            var repl = r.S.Boards.FirstOrDefault(x => x.Serial == serial);
            if (repl is not null && (repl.Id == old.Id || repl.KitId is not null)) throw new ApiError(409, $"Board {serial} is already in a kit.");
            if (repl is null)
            {
                repl = new Board { Serial = serial, Kind = old.Kind, HwRev = old.HwRev };
                r.S.Boards.Add(repl);
                r.S.SaveChanges();
            }
            repl.KitId = old.KitId;
            repl.Kind = old.Kind;
            repl.WarrantyStart = old.WarrantyStart;
            repl.WarrantyMonths = old.WarrantyMonths;
            repl.Status = old.WarrantyStart is not null ? "installed" : old.KitId is not null ? "shipped" : "stock";
            old.KitId = null;
            old.Status = "retired";
            c.ReplacementId = repl.Id;
        }
        else if (status is "repaired" or "denied" && old.Status == "rma")
        {
            var kit = old.KitId is int kid ? r.S.Kits.Find(kid) : null;
            old.Status = kit is null ? "stock" : KitStatus(kit);
        }
        c.Status = status;
        if (b.IsSet("resolution")) c.Resolution = Clean(b.Str("resolution"));
        c.ClosedAt = status == "open" ? null : c.ClosedAt ?? Db.UtcNow();
        r.S.SaveChanges();
        return WarrantyClaimView(r.S, c);
    }

    // ---------- contractors: the QR claim ----------

    /// <summary>The kit a claim code opens, or 404 (counted against wrong guesses).</summary>
    Kit FindClaim(Db s, Principal who, string raw)
    {
        if (!who.IsTech) throw new ApiError(403, "Only a contractor can set up a Fullscope. Ask your contractor.");
        var key = who.UserId is int uid ? $"u{uid}" : $"a{who.AccountId}";
        if (_claimThrottle.Blocked(key)) throw new ApiError(429, "Too many wrong codes. Try again in 15 minutes.");
        var (code, claim) = SplitCode(raw);
        var k = code.Length > 0 ? s.Kits.FirstOrDefault(x => x.Code == code) : null;
        if (k is null || !CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(k.ClaimCode), Encoding.UTF8.GetBytes(claim)))
        {
            _claimThrottle.Fail(key);
            throw new ApiError(404, "That code doesn't match a Fullscope kit. Check the label and try again.");
        }
        return k;
    }

    /// <summary>A kit shipped to a customer with an account is theirs to claim.</summary>
    static bool ShippedElsewhere(Db s, Principal who, Kit k)
    {
        var cust = k.CustomerId is int cid ? s.Customers.Find(cid) : null;
        return cust is not null && cust.AccountId is not null && cust.AccountId != who.AccountId;
    }

    /// <summary>What a scanned QR code is: the kit, its boards, and whether it can be set up.</summary>
    object? ClaimLookup(Req r)
    {
        var who = r.Who;
        var b = Body(r, Field.Str("code", 40, required: true));
        var k = FindClaim(r.S, who, b.Str("code")!);
        var o = new JsonObject
        {
            ["kit"] = k.Code,
            ["boards"] = new JsonArray(KitBoards(r.S, k.Id).Select(x => (JsonNode?)new JsonObject { ["kind"] = x.Kind, ["serial"] = x.Serial }).ToArray()),
            ["system"] = null,
        };
        if (k.ClaimedAt is null) o["status"] = ShippedElsewhere(r.S, who, k) ? "taken" : "available";
        else if (k.AccountId == who.AccountId)
        {
            var system = k.SystemId is int sid ? r.S.Systems.Find(sid) : null;
            o["status"] = "yours";
            o["system"] = system is null ? null : new JsonObject { ["id"] = system.Id, ["name"] = system.Name };
        }
        else o["status"] = "taken";
        return o;
    }

    /// <summary>Set up a kit: a new system on the contractor's account, the boards' warranty starts, and the
    /// homeowner (if given) is invited.</summary>
    object? ClaimKit(Req r)
    {
        var who = r.Who;
        var b = Body(r, Field.Str("code", 40, required: true), Field.Str("system_name", 200, required: true, minLength: 1),
                     Field.Str("address", 500), Field.Str("homeowner_email", 320));
        var k = FindClaim(r.S, who, b.Str("code")!);
        if (k.ClaimedAt is not null || ShippedElsewhere(r.S, who, k))
            throw new ApiError(409, "This kit is already set up. Contact Fullscope if that's wrong.");
        var name = b.Str("system_name")!.Trim();
        if (name.Length == 0) throw new ApiError(422, "Name the system, for example the homeowner's last name.");
        var email = Clean(b.Str("homeowner_email")) is null ? null : CleanEmail(b.Str("homeowner_email")!);
        if (email is not null && r.S.Users.FirstOrDefault(u => u.Email == email) is User user && user.Role != "homeowner")
            throw new ApiError(409, "That email already has a contractor sign-in.");
        var site = k.Code.ToLowerInvariant();
        if (r.S.Systems.Any(x => x.SiteId == site)) throw new ApiError(409, $"A system already reports as {site}. Contact Fullscope.");
        var system = new HvacSystem { AccountId = who.AccountId!.Value, Name = name, SiteId = site, Address = Clean(b.Str("address")), AtmPsia = 14.7 };
        r.S.Systems.Add(system);
        r.S.SaveChanges();
        k.ClaimedAt = Db.UtcNow();
        k.ClaimedBy = who.UserId;
        k.AccountId = who.AccountId;
        k.SystemId = system.Id;
        var today = Time.Today();
        foreach (var board in r.S.Boards.Where(x => x.KitId == k.Id && (x.Status == "stock" || x.Status == "shipped")).ToList())
        {
            board.Status = "installed";
            board.WarrantyStart = today;
            board.WarrantyMonths = board.WarrantyMonths is int m && m != 0 ? m : _settings.WarrantyMonths;
        }
        var cust = CustomerForAccount(r.S, who.AccountId);
        if (k.CustomerId is null && cust is not null) k.CustomerId = cust;
        r.S.SaveChanges();
        var invite = email is null ? null : HomeownerAccess(r.S, who, system, email);
        return new JsonObject
        {
            ["system"] = new JsonObject { ["id"] = system.Id, ["name"] = system.Name, ["site_id"] = system.SiteId, ["address"] = system.Address },
            ["kit"] = k.Code, ["invite"] = invite,
        };
    }
}
