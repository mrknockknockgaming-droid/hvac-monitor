using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Core;
using Microsoft.AspNetCore.Builder;

namespace Fullscope.Cloud.Api;

/// <summary>
/// Quotes (Fullscope Office): a price offer that staff email to a customer or lead with a link to accept it;
/// port of hvaccloud/quotes.py. A quote has lines like an invoice. Sending it (or "Get link", to paste into
/// a text) makes a new private link, {APP_URL}quote.html#/quote/&lt;token&gt;, and an older link stops working;
/// it is issued today and can be answered until valid_until (QUOTE_DAYS later). The customer opens the page,
/// types their name and accepts, or declines with a reason; ORDER_EMAIL gets an email either way. An accepted
/// quote becomes a draft invoice with "Make invoice". Staff can also mark a quote accepted or declined by hand.
/// </summary>
public sealed partial class ApiServer
{
    static readonly string[] QuoteStatuses = ["draft", "sent", "accepted", "declined", "void"];
    static readonly Dictionary<string, string[]> QuoteMoves = new()
    {
        ["draft"] = ["void"], ["sent"] = ["draft", "accepted", "declined", "void"], ["accepted"] = ["sent"],
        ["declined"] = ["draft", "void"], ["void"] = ["draft"],
    };
    const string BadQuoteLink = "This link doesn't work. A newer one may have been sent; ask Fullscope for the latest.";

    void QuoteRoutes(WebApplication app)
    {
        Route(app, "GET", "/api/office/quotes", ListQuotes);
        Route(app, "POST", "/api/office/quotes", AddQuote, body: true);
        Route(app, "GET", "/api/office/quotes/{quote_id}", GetQuote);
        Route(app, "PATCH", "/api/office/quotes/{quote_id}", UpdateQuote, body: true);
        Route(app, "POST", "/api/office/quotes/{quote_id}/send", SendQuote, body: true);
        Route(app, "POST", "/api/office/quotes/{quote_id}/invoice", QuoteToInvoice);
        Route(app, "DELETE", "/api/office/quotes/{quote_id}", DeleteQuote);
        Route(app, "POST", "/api/quote/view", ViewQuote, body: true);
        Route(app, "POST", "/api/quote/answer", AnswerQuote, body: true);
    }

    static string QuoteNumber(Quote q) => $"Q-{1000 + q.Id}";

    static string? Day(DateOnly? d) => d is DateOnly v ? Service.IsoDate(v) : null;

    static List<QuoteLine> QuoteLinesOf(Db s, Quote q) => s.QuoteLines.Where(x => x.QuoteId == q.Id).OrderBy(x => x.Id).ToList();

    static long QuoteTotal(IEnumerable<QuoteLine> lines) => lines.Sum(x => (long)x.Qty * x.UnitCents);

    static bool Expired(Quote q) => q.Status == "sent" && q.ValidUntil is DateOnly v && v < Time.Today();

    static JsonObject QuoteRow(Db s, Quote q, Customer? customer = null)
    {
        var c = customer ?? s.Customers.Find(q.CustomerId)!;
        var lines = QuoteLinesOf(s, q);
        return new JsonObject
        {
            ["id"] = q.Id, ["number"] = QuoteNumber(q), ["status"] = q.Status,
            ["customer"] = new JsonObject { ["id"] = c.Id, ["company"] = c.Company },
            ["created_at"] = Iso(q.CreatedAt), ["issued"] = Day(q.Issued), ["valid_until"] = Day(q.ValidUntil), ["expired"] = Expired(q),
            ["viewed_at"] = Iso(q.ViewedAt), ["answered_at"] = Iso(q.AnsweredAt), ["invoice_id"] = q.InvoiceId,
            ["total_cents"] = QuoteTotal(lines), ["lines"] = lines.Count,
        };
    }

    /// <summary>Counts for the Office header: drafts, sent and waiting, accepted and not invoiced yet.</summary>
    static JsonObject QuoteSummary(Db s)
    {
        var o = new JsonObject { ["draft"] = 0, ["sent"] = 0, ["accepted"] = 0 };
        foreach (var q in s.Quotes.Where(x => x.Status == "draft" || x.Status == "sent" || x.Status == "accepted").ToList())
            if (q.Status != "accepted" || q.InvoiceId is null) o[q.Status] = o[q.Status]!.GetValue<int>() + 1;
        return o;
    }

    static JsonArray CustomerQuotes(Db s, Customer c) =>
        new(s.Quotes.Where(x => x.CustomerId == c.Id).OrderByDescending(x => x.Id).ToList().Select(x => (JsonNode?)QuoteRow(s, x, c)).ToArray());

    static Quote FindQuote(Req r) =>
        r.S.Quotes.Find(Validate.PathInt("quote_id", r.Route("quote_id"))) ?? throw new ApiError(404, "no such quote");

    static JsonArray QuoteLineViews(List<QuoteLine> lines) => new(lines.Select(x => (JsonNode?)new JsonObject
    {
        ["id"] = x.Id, ["description"] = x.Description, ["qty"] = x.Qty, ["unit_cents"] = x.UnitCents, ["amount_cents"] = (long)x.Qty * x.UnitCents,
    }).ToArray());

    JsonObject QuoteDetail(Db s, Quote q)
    {
        var c = s.Customers.Find(q.CustomerId)!;
        var o = QuoteRow(s, q, c);
        o["notes"] = q.Notes;
        o["sent_at"] = Iso(q.SentAt);
        o["emailed"] = q.Emailed;
        o["answered_name"] = q.AnsweredName;
        o["decline_reason"] = q.DeclineReason;
        o["has_link"] = q.TokenHash is not null;
        o["from_email"] = _settings.OrderEmail is { Length: > 0 } from ? from : null;
        o["to"] = new JsonObject { ["company"] = c.Company, ["contact"] = c.Contact, ["email"] = c.Email, ["address"] = c.Address };
        o["lines"] = QuoteLineViews(QuoteLinesOf(s, q));
        return o;
    }

    /// <summary>What the customer sees: not when they opened it, or whether it was emailed.</summary>
    JsonObject QuotePublic(Db s, Quote q)
    {
        var c = s.Customers.Find(q.CustomerId)!;
        var lines = QuoteLinesOf(s, q);
        return new JsonObject
        {
            ["number"] = QuoteNumber(q), ["status"] = q.Status, ["issued"] = Day(q.Issued), ["valid_until"] = Day(q.ValidUntil),
            ["expired"] = Expired(q), ["answered_at"] = Iso(q.AnsweredAt), ["answered_name"] = q.AnsweredName,
            ["to"] = new JsonObject { ["company"] = c.Company, ["contact"] = c.Contact, ["address"] = c.Address }, ["notes"] = q.Notes,
            ["from_email"] = _settings.OrderEmail is { Length: > 0 } from ? from : null, ["lines"] = QuoteLineViews(lines),
            ["total_cents"] = QuoteTotal(lines),
        };
    }

    /// <summary>The new lines for a quote, checked (422) before anything is changed.</summary>
    static List<QuoteLine> CheckQuoteLines(List<Model> lines)
    {
        foreach (var x in lines)
            if (x.Str("description")!.Trim().Length == 0) throw new ApiError(422, "Every line needs a description.");
        return lines.Select(x => new QuoteLine { Description = x.Str("description")!.Trim(), Qty = x.Int("qty") ?? 1, UnitCents = x.Int("unit_cents")!.Value }).ToList();
    }

    static void SetQuoteLines(Db s, Quote q, List<QuoteLine> lines)
    {
        s.QuoteLines.RemoveRange(QuoteLinesOf(s, q));
        foreach (var x in lines)
        {
            x.QuoteId = q.Id;
            s.QuoteLines.Add(x);
        }
    }

    /// <summary>A fresh link; the one sent before stops working.</summary>
    string NewQuoteLink(Quote q)
    {
        var token = Db.TokenUrlSafe(24);
        q.TokenHash = Db.HashKey(token);
        return $"{_settings.AppUrl}quote.html#/quote/{token}";
    }

    string QuoteEmail(Db s, Quote q, string link)
    {
        var c = s.Customers.Find(q.CustomerId)!;
        var lines = QuoteLinesOf(s, q);
        var o = new List<string> { $"Quote {QuoteNumber(q)} from Fullscope", $"Date: {Day(q.Issued)}", $"Valid until: {Day(q.ValidUntil)}", "", $"For: {c.Company}" };
        o.AddRange(new[] { c.Contact, c.Address }.Where(v => !string.IsNullOrEmpty(v))!);
        o.Add("");
        o.AddRange(lines.Select(x => $"{x.Description}: {x.Qty} x {Money(x.UnitCents)} = {Money((long)x.Qty * x.UnitCents)}"));
        o.AddRange(["", $"Total: {Money(QuoteTotal(lines))}"]);
        if (!string.IsNullOrEmpty(q.Notes)) o.AddRange(["", q.Notes]);
        o.AddRange(["", "To accept this quote, open this link:", link, "", "Questions? Reply to this email."]);
        return string.Join("\n", o);
    }

    static Quote QuoteByToken(Db s, string? token)
    {
        if (string.IsNullOrEmpty(token)) throw new ApiError(404, BadQuoteLink);
        var h = Db.HashKey(token);
        return s.Quotes.FirstOrDefault(x => x.TokenHash == h) ?? throw new ApiError(404, BadQuoteLink);
    }

    void TellStaff(Db s, Quote q)
    {
        if (_settings.OrderEmail.Length == 0) return;
        var c = s.Customers.Find(q.CustomerId)!;
        var total = Money(QuoteTotal(QuoteLinesOf(s, q)));
        string subject;
        List<string> lines;
        if (q.Status == "accepted")
        {
            subject = $"[Fullscope] Quote {QuoteNumber(q)} accepted by {c.Company}";
            lines = [$"{q.AnsweredName} accepted quote {QuoteNumber(q)} for {c.Company} ({total})."];
        }
        else
        {
            subject = $"[Fullscope] Quote {QuoteNumber(q)} declined by {c.Company}";
            lines = [$"{c.Company} declined quote {QuoteNumber(q)} ({total}).", "", $"Reason: {q.DeclineReason ?? "-"}"];
        }
        lines.AddRange(["", $"Open it: {_settings.AppUrl}office.html#/quotes/{q.Id}"]);
        SendInvite(_settings.OrderEmail, subject, string.Join("\n", lines));
    }

    // ---------- staff ----------

    /// <summary>Quotes, newest first. "open" = drafts, sent ones waiting for an answer and accepted ones not invoiced.</summary>
    object? ListQuotes(Req r)
    {
        StaffOnly(r.Who);
        var status = Validate.Query(r.Query("status"), Field.Choice("status", ["open", "draft", "sent", "accepted", "declined", "void", "all"]), JsonValue.Create("open")).GetValue<string>();
        IQueryable<Quote> q = r.S.Quotes;
        if (status == "open") q = q.Where(x => x.Status == "draft" || x.Status == "sent" || x.Status == "accepted");
        else if (status != "all") q = q.Where(x => x.Status == status);
        var rows = q.OrderByDescending(x => x.Id).Take(500).ToList().Where(x => status != "open" || x.Status != "accepted" || x.InvoiceId is null);
        return new JsonArray(rows.Select(x => (JsonNode?)QuoteRow(r.S, x)).ToArray());
    }

    /// <summary>A draft for one customer or lead, empty or with the lines given.</summary>
    object? AddQuote(Req r)
    {
        StaffOnly(r.Who);
        var b = Body(r, new Field("customer_id", FieldType.Int) { Required = true, Nullable = false },
                     new Field("lines", FieldType.ListOfDict) { Nullable = false, Default = new JsonArray() }, Field.Str("notes", 2000));
        var errors = new List<JsonObject>();
        var parsed = ParseLines(b.Node("lines"), errors);
        if (errors.Count > 0) throw ApiError.Invalid([.. errors]);
        if (r.S.Customers.Find(b.Int("customer_id")!.Value) is null) throw new ApiError(404, "no such customer");
        var lines = CheckQuoteLines(parsed);
        var q = new Quote { CustomerId = b.Int("customer_id")!.Value, Notes = Clean(b.Str("notes")) };
        r.S.Quotes.Add(q);
        r.S.SaveChanges();
        SetQuoteLines(r.S, q, lines);
        r.S.SaveChanges();
        return QuoteDetail(r.S, q);
    }

    object? GetQuote(Req r)
    {
        StaffOnly(r.Who);
        return QuoteDetail(r.S, FindQuote(r));
    }

    /// <summary>Edit a draft; move the valid-until date; mark it answered by hand, back to draft, or void.</summary>
    object? UpdateQuote(Req r)
    {
        StaffOnly(r.Who);
        var b = Body(r, new Field("lines", FieldType.ListOfDict), Field.Str("notes", 2000), new Field("valid_until", FieldType.Date),
                     Field.Choice("status", QuoteStatuses), Field.Str("answered_name", 120), Field.Str("decline_reason", 1000));
        var errors = new List<JsonObject>();
        var parsed = ParseLines(b.Node("lines"), errors);
        if (errors.Count > 0) throw ApiError.Invalid([.. errors]);
        var q = FindQuote(r);
        List<QuoteLine>? lines = null;                                      // nothing is saved until every check passed
        if ((b.IsSet("lines") && b.Node("lines") is not null) || b.IsSet("notes"))
        {
            if (q.Status != "draft") throw new ApiError(409, "Only a draft can be changed. Move it back to draft first.");
            if (b.Node("lines") is not null) lines = CheckQuoteLines(parsed);
            if (b.IsSet("notes")) q.Notes = Clean(b.Str("notes"));
        }
        if (b.IsSet("valid_until"))
        {
            if (q.Status is not ("draft" or "sent")) throw new ApiError(409, $"This quote is {q.Status}.");
            q.ValidUntil = b.Date("valid_until");
        }
        if (b.Str("status") is string status && status != q.Status)
        {
            if (!QuoteMoves[q.Status].Contains(status)) throw new ApiError(409, $"This quote is {q.Status}; it can't be marked {status}.");
            if (q.Status == "accepted" && q.InvoiceId is not null) throw new ApiError(409, "It has an invoice already.");
            if (status is "accepted" or "declined")                         // the customer answered by phone or email
            {
                q.AnsweredAt = Db.UtcNow();
                q.AnsweredIp = null;
                q.AnsweredName = Clean(b.Str("answered_name"));
                q.DeclineReason = status == "declined" ? Clean(b.Str("decline_reason")) : null;
            }
            else
            {
                q.AnsweredAt = null;
                q.AnsweredName = q.AnsweredIp = q.DeclineReason = null;
            }
            if (status is "draft" or "void") q.TokenHash = null;            // the link stops working
            q.Status = status;
        }
        if (lines is not null) SetQuoteLines(r.S, q, lines);
        r.S.SaveChanges();
        return QuoteDetail(r.S, q);
    }

    /// <summary>Email the quote with a new link (or only make the link, to paste into a text). Sending again
    /// replaces the link and keeps the dates.</summary>
    object? SendQuote(Req r)
    {
        StaffOnly(r.Who);
        var b = OptionalBody(r, Field.Bool("email", true));
        var q = FindQuote(r);
        if (q.Status is not ("draft" or "sent")) throw new ApiError(409, $"This quote is {q.Status}.");
        if (QuoteLinesOf(r.S, q).Count == 0) throw new ApiError(422, "Add a line first.");
        var c = r.S.Customers.Find(q.CustomerId)!;
        bool email = b?.Bool("email") ?? true;
        if (email && string.IsNullOrEmpty(c.Email))
            throw new ApiError(422, "The customer has no email address. Add one on the Customers page, or use Get link.");
        q.Issued ??= Time.Today();
        if (q.ValidUntil is not DateOnly v || v < Time.Today()) q.ValidUntil = Time.Today().AddDays(_settings.QuoteDays);
        var link = NewQuoteLink(q);
        if (email)
        {
            if (!SendInvite(c.Email!, $"Fullscope quote {QuoteNumber(q)} for {c.Company}", QuoteEmail(r.S, q, link)))
                throw new ApiError(503, "Email is off on the server, so it wasn't sent. Use Get link instead.");
            q.Emailed = true;
        }
        q.Status = "sent";
        q.SentAt = Db.UtcNow();
        r.S.SaveChanges();
        var o = QuoteDetail(r.S, q);
        o["link"] = link;
        return o;
    }

    /// <summary>A draft invoice with the accepted quote's lines.</summary>
    object? QuoteToInvoice(Req r)
    {
        StaffOnly(r.Who);
        var q = FindQuote(r);
        if (q.Status != "accepted") throw new ApiError(409, "Only an accepted quote can become an invoice.");
        if (q.InvoiceId is not null) throw new ApiError(409, "It has an invoice already.");
        var inv = new Invoice { CustomerId = q.CustomerId, Notes = $"Quote {QuoteNumber(q)}" + (q.Notes is { } n ? "\n" + n : "") };
        r.S.Invoices.Add(inv);
        r.S.SaveChanges();
        foreach (var x in QuoteLinesOf(r.S, q))
            r.S.InvoiceLines.Add(new InvoiceLine { InvoiceId = inv.Id, Description = x.Description, Qty = x.Qty, UnitCents = x.UnitCents });
        q.InvoiceId = inv.Id;
        r.S.SaveChanges();
        var o = QuoteDetail(r.S, q);
        o["invoice_id"] = inv.Id;
        return o;
    }

    /// <summary>Throw away a draft. One that was sent is voided instead.</summary>
    object? DeleteQuote(Req r)
    {
        StaffOnly(r.Who);
        var q = FindQuote(r);
        if (q.Status != "draft" || q.SentAt is not null) throw new ApiError(409, "Only a draft that was never sent can be deleted. Void it instead.");
        r.S.QuoteLines.RemoveRange(QuoteLinesOf(r.S, q));
        r.S.Quotes.Remove(q);
        r.S.SaveChanges();
        return new JsonObject { ["ok"] = true };
    }

    // ---------- public: the customer's link ----------

    /// <summary>The quote behind a link (the token travels in the body, never in a URL the server logs).</summary>
    object? ViewQuote(Req r)
    {
        var b = Body(r, Field.Str("token", 100, required: true));
        var q = QuoteByToken(r.S, b.Str("token"));
        if (q.Status is not ("sent" or "accepted" or "declined")) throw new ApiError(404, BadQuoteLink);
        if (q.ViewedAt is null)
        {
            q.ViewedAt = Db.UtcNow();
            r.S.SaveChanges();
        }
        return QuotePublic(r.S, q);
    }

    /// <summary>Accept (with the name of the person accepting) or decline (with a reason, if they give one).</summary>
    object? AnswerQuote(Req r)
    {
        var b = Body(r, Field.Str("token", 100, required: true), Field.Bool("accept", required: true),
                     Field.Str("name", 120), Field.Str("reason", 1000));
        var q = QuoteByToken(r.S, b.Str("token"));
        if (q.Status is "accepted" or "declined") throw new ApiError(409, $"This quote was {q.Status} already.");
        if (q.Status != "sent") throw new ApiError(404, BadQuoteLink);
        if (Expired(q)) throw new ApiError(410, $"This quote expired on {Day(q.ValidUntil)}. Ask Fullscope for a new one.");
        var name = Clean(b.Str("name"));
        bool accept = b.Bool("accept")!.Value;
        if (accept && name is null) throw new ApiError(422, "Type your name to accept.");
        q.Status = accept ? "accepted" : "declined";
        q.AnsweredAt = Db.UtcNow();
        q.AnsweredName = name;
        var ip = ClientIp(r.Http);
        q.AnsweredIp = ip.Length > 64 ? ip[..64] : ip;
        q.DeclineReason = accept ? null : Clean(b.Str("reason"));
        r.S.SaveChanges();
        TellStaff(r.S, q);
        return QuotePublic(r.S, q);
    }
}
