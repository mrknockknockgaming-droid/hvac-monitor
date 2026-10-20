using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Fullscope.Cloud.Data;
using Fullscope.Core;
using Microsoft.AspNetCore.Builder;

namespace Fullscope.Cloud.Api;

/// <summary>
/// Billing (Fullscope Office): invoices that staff send by email and mark paid by hand; port of
/// hvaccloud/billing.py. No card payments. Prices: each kit once (when shipped to a customer) and each
/// system monthly. The list prices are the landing page's web/pricing.json (dollars); a customer can have
/// their own. "Draft this month's invoices" makes one draft per active customer with a line per kit shipped
/// and not billed yet and a line per system on their account. Drafts can be edited, then sent (emailed;
/// issued today, due in INVOICE_DAYS) or marked sent by hand (printed and posted), then marked paid.
/// Amounts are whole cents.
/// </summary>
public sealed partial class ApiServer
{
    static readonly string[] InvoiceStatuses = ["draft", "sent", "paid", "void"];
    static readonly Dictionary<string, string[]> InvoiceMoves = new()
    {
        ["draft"] = ["sent", "paid", "void"], ["sent"] = ["draft", "paid", "void"], ["paid"] = ["sent"], ["void"] = ["draft"],
    };
    const int MaxCents = 100_000_000;
    const int MaxLines = 200;

    [GeneratedRegex(@"^(\d{4})-(0[1-9]|1[0-2])$")]
    private static partial Regex Period();

    static readonly Field[] LineFields =
    [
        Field.Str("description", 200, required: true, minLength: 1),
        Field.Int("qty", ge: 1, le: 10000, nullable: false) with { Default = JsonValue.Create(1L) },
        Field.Int("unit_cents", ge: -MaxCents, le: MaxCents, nullable: false) with { Required = true },
        Field.Int("kit_id"), Field.Int("system_id"),
    ];

    void BillingRoutes(WebApplication app)
    {
        Route(app, "GET", "/api/office/invoices", ListInvoices);
        Route(app, "POST", "/api/office/invoices", AddInvoice, body: true);
        Route(app, "POST", "/api/office/invoices/run", RunInvoices, body: true);
        Route(app, "GET", "/api/office/invoices/{invoice_id}", GetInvoice);
        Route(app, "PATCH", "/api/office/invoices/{invoice_id}", UpdateInvoice, body: true);
        Route(app, "POST", "/api/office/invoices/{invoice_id}/send", SendInvoice);
        Route(app, "DELETE", "/api/office/invoices/{invoice_id}", DeleteInvoice);
    }

    /// <summary>$1,234.56 (and -$5.00 for a credit).</summary>
    public static string Money(long cents)
    {
        long c = Math.Abs(cents);
        return (cents < 0 ? "-" : "") + "$" + (c / 100).ToString("N0", CultureInfo.InvariantCulture) + "." + (c % 100).ToString("00", CultureInfo.InvariantCulture);
    }

    public static string MonthName(string period) =>
        CultureInfo.InvariantCulture.DateTimeFormat.GetMonthName(int.Parse(period[5..], CultureInfo.InvariantCulture)) + " " + period[..4];

    /// <summary>The landing page's prices in cents; null when not set.</summary>
    (int? Kit, int? Monthly) ListPrices()
    {
        JsonObject? p = null;
        if (_settings.WebDir.Length > 0)
            try { p = JsonNode.Parse(File.ReadAllText(Path.Combine(_settings.WebDir, "pricing.json"))) as JsonObject; }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException or JsonException) { }
        static int? Cents(JsonNode? v) =>
            v is JsonValue jv && jv.GetValueKind() == JsonValueKind.Number ? (int)Math.Round(jv.GetValue<double>() * 100, MidpointRounding.ToEven) : null;
        return (Cents(p?["kit"]), Cents(p?["monitoring"]));
    }

    JsonObject ListPricesView()
    {
        var (kit, monthly) = ListPrices();
        return new JsonObject { ["kit_cents"] = kit, ["monthly_cents"] = monthly };
    }

    static List<InvoiceLine> LinesOf(Db s, Invoice inv) => s.InvoiceLines.Where(x => x.InvoiceId == inv.Id).OrderBy(x => x.Id).ToList();

    static long Total(IEnumerable<InvoiceLine> lines) => lines.Sum(x => (long)x.Qty * x.UnitCents);

    static bool Overdue(Invoice inv) => inv.Status == "sent" && inv.Due is DateOnly due && due < Time.Today();

    static JsonObject InvoiceRow(Db s, Invoice inv, Customer? customer = null)
    {
        var c = customer ?? s.Customers.Find(inv.CustomerId)!;
        var lines = LinesOf(s, inv);
        return new JsonObject
        {
            ["id"] = inv.Id, ["number"] = 1000 + inv.Id, ["status"] = inv.Status, ["period"] = inv.Period,
            ["customer"] = new JsonObject { ["id"] = c.Id, ["company"] = c.Company }, ["created_at"] = Iso(inv.CreatedAt),
            ["issued"] = inv.Issued is DateOnly i ? Service.IsoDate(i) : null, ["due"] = inv.Due is DateOnly d ? Service.IsoDate(d) : null,
            ["paid_on"] = inv.PaidOn is DateOnly p ? Service.IsoDate(p) : null, ["overdue"] = Overdue(inv),
            ["total_cents"] = Total(lines), ["lines"] = lines.Count,
        };
    }

    /// <summary>Counts for the Office header: drafts, sent and unpaid, overdue, and what is owed.</summary>
    static JsonObject InvoiceSummary(Db s)
    {
        int draft = 0, sent = 0, overdue = 0;
        long unpaid = 0;
        foreach (var inv in s.Invoices.Where(x => x.Status == "draft" || x.Status == "sent").ToList())
        {
            if (inv.Status == "draft") { draft++; continue; }
            sent++;
            unpaid += Total(LinesOf(s, inv));
            if (Overdue(inv)) overdue++;
        }
        return new JsonObject { ["draft"] = draft, ["sent"] = sent, ["overdue"] = overdue, ["unpaid_cents"] = unpaid };
    }

    static JsonArray CustomerInvoices(Db s, Customer c) =>
        new(s.Invoices.Where(x => x.CustomerId == c.Id).OrderByDescending(x => x.Id).ToList().Select(x => (JsonNode?)InvoiceRow(s, x, c)).ToArray());

    static Invoice FindInvoice(Req r) =>
        r.S.Invoices.Find(Validate.PathInt("invoice_id", r.Route("invoice_id"))) ?? throw new ApiError(404, "no such invoice");

    JsonObject InvoiceDetail(Db s, Invoice inv)
    {
        var c = s.Customers.Find(inv.CustomerId)!;
        var o = InvoiceRow(s, inv, c);
        o["notes"] = inv.Notes;
        o["paid_note"] = inv.PaidNote;
        o["sent_at"] = Iso(inv.SentAt);
        o["emailed"] = inv.Emailed;
        o["pay_note"] = _settings.InvoiceNote is { Length: > 0 } note ? note : null;
        o["from_email"] = _settings.OrderEmail is { Length: > 0 } from ? from : null;
        o["bill_to"] = new JsonObject { ["company"] = c.Company, ["contact"] = c.Contact, ["email"] = c.Email, ["address"] = c.Address };
        o["lines"] = new JsonArray(LinesOf(s, inv).Select(x => (JsonNode?)new JsonObject
        {
            ["id"] = x.Id, ["description"] = x.Description, ["qty"] = x.Qty, ["unit_cents"] = x.UnitCents,
            ["amount_cents"] = (long)x.Qty * x.UnitCents, ["kit_id"] = x.KitId, ["system_id"] = x.SystemId,
        }).ToArray());
        return o;
    }

    /// <summary>A list of line objects checked like pydantic's list[LineIn] (locations body.lines.i.x).</summary>
    static List<Model> ParseLines(JsonNode? node, List<JsonObject> errors)
    {
        var lines = new List<Model>();
        if (node is not JsonArray items) return lines;
        if (items.Count > MaxLines)
        {
            errors.Add(ApiError.Problem("too_long", $"List should have at most {MaxLines} items after validation, not {items.Count}", items, "body", "lines"));
            return lines;
        }
        for (int i = 0; i < items.Count; i++)
        {
            try { lines.Add(Validate.Body(items[i], true, LineFields)); }
            catch (ApiError e) when (e.Detail is JsonArray list)
            {
                foreach (var p in list.OfType<JsonObject>())                    // body.x -> body.lines.i.x
                {
                    var loc = p["loc"]!.AsArray().Select(x => x!.DeepClone()).ToList();
                    p["loc"] = new JsonArray([JsonValue.Create("body"), JsonValue.Create("lines"), JsonValue.Create(i), .. loc.Skip(1)]);
                    errors.Add((JsonObject)p.DeepClone());
                }
            }
        }
        return lines;
    }

    /// <summary>The new lines for an invoice, checked (422) before anything is changed.</summary>
    static List<InvoiceLine> CheckLines(Db s, List<Model> lines)
    {
        foreach (var x in lines)
        {
            if (x.Str("description")!.Trim().Length == 0) throw new ApiError(422, "Every line needs a description.");
            if (x.Int("kit_id") is int kid && s.Kits.Find(kid) is null) throw new ApiError(422, "no such kit");
            if (x.Int("system_id") is int sid && s.Systems.Find(sid) is null) throw new ApiError(422, "no such system");
        }
        return lines.Select(x => new InvoiceLine
        {
            Description = x.Str("description")!.Trim(), Qty = x.Int("qty") ?? 1, UnitCents = x.Int("unit_cents")!.Value,
            KitId = x.Int("kit_id"), SystemId = x.Int("system_id"),
        }).ToList();
    }

    /// <summary>Replace an invoice's lines (it is saved already, so it has its id).</summary>
    static void SetLines(Db s, Invoice inv, List<InvoiceLine> lines)
    {
        s.InvoiceLines.RemoveRange(LinesOf(s, inv));
        foreach (var x in lines)
        {
            x.InvoiceId = inv.Id;
            s.InvoiceLines.Add(x);
        }
    }

    /// <summary>Sent or paid: it needs a line, gets today's date and a due date.</summary>
    void Issue(Invoice inv, int lineCount)
    {
        if (lineCount == 0) throw new ApiError(422, "Add a line first.");
        inv.Issued ??= Time.Today();
        inv.Due ??= inv.Issued.Value.AddDays(_settings.InvoiceDays);
    }

    string InvoiceEmail(Db s, Invoice inv)
    {
        var c = s.Customers.Find(inv.CustomerId)!;
        var lines = LinesOf(s, inv);
        var o = new List<string>
        {
            $"Invoice {1000 + inv.Id} from Fullscope", $"Date: {Service.IsoDate(inv.Issued!.Value)}", $"Due: {Service.IsoDate(inv.Due!.Value)}", "",
            $"Bill to: {c.Company}",
        };
        o.AddRange(new[] { c.Contact, c.Address }.Where(v => !string.IsNullOrEmpty(v))!);
        o.Add("");
        o.AddRange(lines.Select(x => $"{x.Description}: {x.Qty} x {Money(x.UnitCents)} = {Money((long)x.Qty * x.UnitCents)}"));
        o.AddRange(["", $"Total due: {Money(Total(lines))}"]);
        foreach (var v in new[] { inv.Notes, _settings.InvoiceNote })
            if (!string.IsNullOrEmpty(v)) o.AddRange(["", v]);
        o.AddRange(["", "Questions about this invoice? Reply to this email."]);
        return string.Join("\n", o);
    }

    /// <summary>Invoices, newest first. "open" = drafts and sent ones not paid yet.</summary>
    object? ListInvoices(Req r)
    {
        StaffOnly(r.Who);
        var status = Validate.Query(r.Query("status"), Field.Choice("status", ["open", "draft", "sent", "paid", "void", "all"]), JsonValue.Create("open")).GetValue<string>();
        IQueryable<Invoice> q = r.S.Invoices;
        if (status == "open") q = q.Where(x => x.Status == "draft" || x.Status == "sent");
        else if (status != "all") q = q.Where(x => x.Status == status);
        var rows = q.OrderByDescending(x => x.Id).Take(500).ToList();
        return new JsonArray(rows.Select(x => (JsonNode?)InvoiceRow(r.S, x)).ToArray());
    }

    /// <summary>A draft for one customer, empty or with the lines given.</summary>
    object? AddInvoice(Req r)
    {
        StaffOnly(r.Who);
        var b = Body(r, new Field("customer_id", FieldType.Int) { Required = true, Nullable = false },
                     new Field("lines", FieldType.ListOfDict) { Nullable = false, Default = new JsonArray() }, Field.Str("notes", 2000));
        var errors = new List<JsonObject>();
        var lines = ParseLines(b.Node("lines"), errors);
        if (errors.Count > 0) throw ApiError.Invalid([.. errors]);
        if (r.S.Customers.Find(b.Int("customer_id")!.Value) is null) throw new ApiError(404, "no such customer");
        var rows = CheckLines(r.S, lines);
        var inv = new Invoice { CustomerId = b.Int("customer_id")!.Value, Notes = Clean(b.Str("notes")) };
        r.S.Invoices.Add(inv);
        r.S.SaveChanges();
        SetLines(r.S, inv, rows);
        r.S.SaveChanges();
        return InvoiceDetail(r.S, inv);
    }

    /// <summary>This month's drafts: one per active customer (or the one given) with a line per kit shipped to them
    /// and not billed yet, and one per system on their account. Customers billed for the month already, or
    /// with nothing to bill, are skipped.</summary>
    object? RunInvoices(Req r)
    {
        StaffOnly(r.Who);
        var b = Body(r, Field.Str("period", 7, required: true), Field.Int("customer_id"));
        var period = b.Str("period")!.Trim();
        if (!Period().IsMatch(period)) throw new ApiError(422, "A month looks like 2026-10.");
        List<Customer> todo;
        if (b.Int("customer_id") is int cid) todo = [r.S.Customers.Find(cid) ?? throw new ApiError(404, "no such customer")];
        else todo = r.S.Customers.Where(c => c.Status == "active").OrderBy(c => c.Company).ThenBy(c => c.Id).ToList();
        var billedKits = (from x in r.S.InvoiceLines
                          join inv in r.S.Invoices on x.InvoiceId equals inv.Id
                          where x.KitId != null && inv.Status != "void"
                          select x.KitId!.Value).ToHashSet();
        var (listKit, listMonthly) = ListPrices();
        var created = new List<Invoice>();
        var skipped = new JsonArray();
        bool missing = false;
        var month = MonthName(period);
        foreach (var c in todo)
        {
            var who = new JsonObject { ["id"] = c.Id, ["company"] = c.Company };
            if (r.S.Invoices.Any(x => x.CustomerId == c.Id && x.Period == period && x.Status != "void"))
            {
                skipped.Add(new JsonObject { ["customer"] = who, ["reason"] = $"Already has an invoice for {month}." });
                continue;
            }
            int? kitPrice = c.KitCents ?? listKit, monthly = c.MonthlyCents ?? listMonthly;
            var lines = new List<InvoiceLine>();
            foreach (var k in r.S.Kits.Where(k => k.CustomerId == c.Id && k.ShippedAt != null).OrderBy(k => k.ShippedAt).ThenBy(k => k.Id).ToList())
            {
                if (billedKits.Contains(k.Id)) continue;
                lines.Add(new InvoiceLine { Description = $"Fullscope kit {k.Code}", UnitCents = kitPrice ?? 0, KitId = k.Id });
                missing |= kitPrice is null;
            }
            if (c.AccountId is int aid)
                foreach (var system in r.S.Systems.Where(x => x.AccountId == aid).OrderBy(x => x.Name).ThenBy(x => x.Id).ToList())
                {
                    lines.Add(new InvoiceLine { Description = $"Monitoring, {month}: {system.Name}", UnitCents = monthly ?? 0, SystemId = system.Id });
                    missing |= monthly is null;
                }
            if (lines.Count == 0)
            {
                skipped.Add(new JsonObject { ["customer"] = who, ["reason"] = "Nothing to bill." });
                continue;
            }
            var inv = new Invoice { CustomerId = c.Id, Period = period };
            r.S.Invoices.Add(inv);
            r.S.SaveChanges();
            SetLines(r.S, inv, lines);
            r.S.SaveChanges();
            created.Add(inv);
        }
        return new JsonObject
        {
            ["created"] = new JsonArray(created.Select(x => (JsonNode?)InvoiceRow(r.S, x)).ToArray()), ["skipped"] = skipped, ["missing_prices"] = missing,
        };
    }

    object? GetInvoice(Req r)
    {
        StaffOnly(r.Who);
        return InvoiceDetail(r.S, FindInvoice(r));
    }

    /// <summary>Edit a draft's lines and notes; change the due date; move it along: draft -> sent -> paid, or void.</summary>
    object? UpdateInvoice(Req r)
    {
        StaffOnly(r.Who);
        var b = Body(r, new Field("lines", FieldType.ListOfDict), Field.Str("notes", 2000), new Field("due", FieldType.Date),
                     Field.Choice("status", InvoiceStatuses), new Field("paid_on", FieldType.Date), Field.Str("paid_note", 200));
        var errors = new List<JsonObject>();
        var parsed = ParseLines(b.Node("lines"), errors);
        if (errors.Count > 0) throw ApiError.Invalid([.. errors]);
        var inv = FindInvoice(r);
        List<InvoiceLine>? lines = null;                                    // nothing is saved until every check passed
        if ((b.IsSet("lines") && b.Node("lines") is not null) || b.IsSet("notes"))
        {
            if (inv.Status != "draft") throw new ApiError(409, "Only a draft can be changed. Move it back to draft first.");
            if (b.Node("lines") is not null) lines = CheckLines(r.S, parsed);
            if (b.IsSet("notes")) inv.Notes = Clean(b.Str("notes"));
        }
        if (b.IsSet("due"))
        {
            if (inv.Status is "paid" or "void") throw new ApiError(409, "This invoice is closed.");
            inv.Due = b.Date("due");
        }
        if (b.Str("status") is string status && status != inv.Status)
        {
            if (!InvoiceMoves[inv.Status].Contains(status)) throw new ApiError(409, $"A {inv.Status} invoice can't be marked {status}.");
            if (status is "sent" or "paid") Issue(inv, lines?.Count ?? LinesOf(r.S, inv).Count);
            if (status == "paid")
            {
                if (b.Date("paid_on") is DateOnly on && on > Time.Today()) throw new ApiError(422, "The paid date can't be in the future.");
                inv.PaidOn = b.Date("paid_on") ?? Time.Today();
            }
            else if (inv.Status == "paid")                                  // paid by mistake
            {
                inv.PaidOn = null;
                inv.PaidNote = null;
            }
            inv.Status = status;
        }
        if (b.IsSet("paid_note") && inv.Status == "paid") inv.PaidNote = Clean(b.Str("paid_note"));
        if (lines is not null) SetLines(r.S, inv, lines);
        r.S.SaveChanges();
        return InvoiceDetail(r.S, inv);
    }

    /// <summary>Email the invoice to the customer (again, if it was sent before).</summary>
    object? SendInvoice(Req r)
    {
        StaffOnly(r.Who);
        var inv = FindInvoice(r);
        if (inv.Status is not ("draft" or "sent")) throw new ApiError(409, $"This invoice is {inv.Status}.");
        var c = r.S.Customers.Find(inv.CustomerId)!;
        if (string.IsNullOrEmpty(c.Email)) throw new ApiError(422, "The customer has no email address. Add one on the Customers page.");
        Issue(inv, LinesOf(r.S, inv).Count);
        if (!SendInvite(c.Email, $"Fullscope invoice {1000 + inv.Id} for {c.Company}", InvoiceEmail(r.S, inv)))
            throw new ApiError(503, "Email is off on the server, so it wasn't sent. Print it instead, then mark it sent.");
        inv.Status = "sent";
        inv.SentAt = Db.UtcNow();
        inv.Emailed = true;
        r.S.SaveChanges();
        return InvoiceDetail(r.S, inv);
    }

    /// <summary>Throw away a draft. Sent invoices are voided instead, so their numbers stay accounted for.</summary>
    object? DeleteInvoice(Req r)
    {
        StaffOnly(r.Who);
        var inv = FindInvoice(r);
        if (inv.Status != "draft") throw new ApiError(409, "Only a draft can be deleted. Void it instead.");
        r.S.InvoiceLines.RemoveRange(LinesOf(r.S, inv));
        r.S.Invoices.Remove(inv);
        r.S.SaveChanges();
        return new JsonObject { ["ok"] = true };
    }
}
