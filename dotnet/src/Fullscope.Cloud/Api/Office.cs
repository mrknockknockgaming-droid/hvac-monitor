using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Microsoft.AspNetCore.Builder;

namespace Fullscope.Cloud.Api;

/// <summary>
/// Fullscope Office: the business side, for Fullscope's own staff (users.staff); port of
/// hvaccloud/office.py. Inbox: every order request (landing page), contact message (landing page)
/// and support request (contractors, from the app) is a ticket; staff reply by email from the page
/// or add internal notes, and each new ticket is also emailed to ORDER_EMAIL. Customers: the
/// contractor companies Fullscope sells to, linked to their sign-in account once they have one.
/// </summary>
public sealed partial class ApiServer
{
    static readonly string[] TicketStatuses = ["new", "open", "waiting", "closed"];
    static readonly string[] CustomerStatuses = ["lead", "active", "paused", "closed"];
    static readonly Dictionary<string, string> TopicWord = new() { ["sales"] = "Sales question", ["support"] = "Support request", ["other"] = "Question" };
    readonly Throttle _contactThrottle = new(limit: 5, window: 60 * 60);   // landing-page contact messages per address per hour

    void OfficeRoutes(WebApplication app)
    {
        Route(app, "POST", "/api/contact", Contact, body: true);
        Route(app, "POST", "/api/support", AskSupport, body: true);
        Route(app, "GET", "/api/support", MySupport);
        Route(app, "GET", "/api/office/summary", OfficeSummary);
        Route(app, "GET", "/api/office/inbox", Inbox);
        Route(app, "GET", "/api/office/inbox/{ticket_id}", GetTicket);
        Route(app, "PATCH", "/api/office/inbox/{ticket_id}", UpdateTicket, body: true);
        Route(app, "POST", "/api/office/inbox/{ticket_id}/messages", AddTicketMessage, body: true);
        Route(app, "POST", "/api/office/inbox/{ticket_id}/customer", TicketToCustomer);
        Route(app, "GET", "/api/office/customers", ListCustomers);
        Route(app, "POST", "/api/office/customers", AddCustomer, body: true);
        Route(app, "GET", "/api/office/customers/{customer_id}", GetCustomer);
        Route(app, "PATCH", "/api/office/customers/{customer_id}", UpdateCustomer, body: true);
        Route(app, "POST", "/api/office/customers/{customer_id}/account", MakeCustomerAccount, body: true);
    }

    static void StaffOnly(Principal who)
    {
        if (!who.Staff) throw new ApiError(403, "for Fullscope staff only");
    }

    static string? Clean(string? v) => v?.Trim() is { Length: > 0 } t ? t : null;

    /// <summary>New ticket: a copy to the business inbox (ORDER_EMAIL).</summary>
    void Notify(Ticket t)
    {
        if (_settings.OrderEmail.Length == 0) return;
        var lines = new List<string> { $"New {t.Kind} #{t.Id} in the Fullscope Office inbox.", "", $"From: {t.Name ?? "-"} <{t.Email}>" };
        if (!string.IsNullOrEmpty(t.Company)) lines.Add($"Company: {t.Company}");
        if (!string.IsNullOrEmpty(t.Phone)) lines.Add($"Phone: {t.Phone}");
        lines.AddRange(["", string.IsNullOrEmpty(t.Body) ? "-" : t.Body, "", $"Open it: {_settings.AppUrl}office.html#/inbox/{t.Id}"]);
        SendInvite(_settings.OrderEmail, $"[Fullscope #{t.Id}] {t.Subject}", string.Join("\n", lines));
    }

    static int? CustomerForAccount(Db s, int? accountId) =>
        accountId is null ? null : s.Customers.Where(c => c.AccountId == accountId).OrderBy(c => c.Id).Select(c => (int?)c.Id).FirstOrDefault();

    // ---------- public: the landing page's contact form ----------

    /// <summary>A question from the landing page: saved in the Office inbox and emailed to ORDER_EMAIL.</summary>
    object? Contact(Req r)
    {
        var b = Body(r, Field.Str("name", 200, required: true, minLength: 1), Field.Str("email", 320, required: true), Field.Str("phone", 40),
                     Field.Str("company", 200), Field.Choice("topic", ["sales", "support", "other"]) with { Default = JsonValue.Create("other"), Nullable = false },
                     Field.Str("message", 4000, required: true, minLength: 1), Field.Str("website", 200));
        if (!string.IsNullOrEmpty(b.Str("website"))) return new JsonObject { ["ok"] = true };   // a bot filled in the hidden field
        var ip = ClientIp(r.Http);
        if (_contactThrottle.Blocked(ip)) throw new ApiError(429, "Too many messages from this address. Try again in an hour.");
        var email = CleanEmail(b.Str("email")!);
        string name = b.Str("name")!.Trim(), message = b.Str("message")!.Trim();
        if (name.Length == 0 || message.Length == 0) throw new ApiError(422, "Name and message are required.");
        _contactThrottle.Fail(ip);
        var company = Clean(b.Str("company"));
        var t = new Ticket
        {
            Kind = "contact", Subject = $"{TopicWord[b.Str("topic") ?? "other"]} from {company ?? name}", Name = name, Email = email,
            Phone = Clean(b.Str("phone")), Company = company, Body = message,
        };
        r.S.Tickets.Add(t);
        r.S.SaveChanges();
        Notify(t);
        return new JsonObject { ["ok"] = true, ["id"] = t.Id };
    }

    // ---------- contractors: support requests from the app ----------

    /// <summary>A contractor asks Fullscope for help. Homeowners ask their contractor instead.</summary>
    object? AskSupport(Req r)
    {
        var who = r.Who;
        var b = Body(r, Field.Str("subject", 200, required: true, minLength: 1), Field.Str("message", 4000, required: true, minLength: 1),
                     Field.Int("system_id"));
        if (!who.IsTech) throw new ApiError(403, "Please contact your heating and cooling contractor.");
        string subject = b.Str("subject")!.Trim(), message = b.Str("message")!.Trim();
        if (subject.Length == 0 || message.Length == 0) throw new ApiError(422, "Subject and message are required.");
        HvacSystem? system = null;
        if (b.Int("system_id") is int sid)
            system = r.S.Systems.FirstOrDefault(x => x.Id == sid && x.AccountId == who.AccountId) ?? throw new ApiError(404, "no such system");
        var user = who.UserId is int uid ? r.S.Users.Find(uid) : null;
        var acct = r.S.Accounts.Find(who.AccountId!.Value)!;
        var text = message + (system is null ? "" : $"\n\nSystem: {system.Name} ({system.SiteId})");
        var t = new Ticket
        {
            Kind = "support", Subject = subject, Name = user?.Name, Email = user?.Email ?? acct.Email, Company = acct.Name, Body = text,
            AccountId = acct.Id, SystemId = system?.Id, UserId = user?.Id, CustomerId = CustomerForAccount(r.S, acct.Id),
        };
        r.S.Tickets.Add(t);
        r.S.SaveChanges();
        Notify(t);
        return SupportView(r.S, t);
    }

    /// <summary>The account's support requests, newest first, with Fullscope's replies (not internal notes).</summary>
    object? MySupport(Req r)
    {
        var who = r.Who;
        if (!who.IsTech) throw new ApiError(403, "Please contact your heating and cooling contractor.");
        var rows = r.S.Tickets.Where(t => t.Kind == "support" && t.AccountId == who.AccountId)
                    .OrderByDescending(t => t.CreatedAt).ThenByDescending(t => t.Id).Take(50).ToList();
        return new JsonArray(rows.Select(t => (JsonNode?)SupportView(r.S, t)).ToArray());
    }

    static JsonObject SupportView(Db s, Ticket t)
    {
        var replies = s.TicketMessages.Where(m => m.TicketId == t.Id && m.Kind == "reply").OrderBy(m => m.Id).ToList();
        return new JsonObject
        {
            ["id"] = t.Id, ["subject"] = t.Subject, ["body"] = t.Body, ["status"] = t.Status == "closed" ? "closed" : "open",
            ["created_at"] = Iso(t.CreatedAt), ["name"] = t.Name, ["email"] = t.Email,
            ["replies"] = new JsonArray(replies.Select(m => (JsonNode?)new JsonObject { ["body"] = m.Body, ["at"] = Iso(m.CreatedAt) }).ToArray()),
        };
    }

    // ---------- staff: inbox ----------

    static JsonObject TicketRow(Db s, Ticket t, Dictionary<int, Customer> customers)
    {
        int n = s.TicketMessages.Count(m => m.TicketId == t.Id);
        var c = t.CustomerId is int cid ? customers.GetValueOrDefault(cid) : null;
        return new JsonObject
        {
            ["id"] = t.Id, ["kind"] = t.Kind, ["status"] = t.Status, ["subject"] = t.Subject, ["name"] = t.Name, ["email"] = t.Email,
            ["company"] = t.Company, ["created_at"] = Iso(t.CreatedAt), ["updated_at"] = Iso(t.UpdatedAt), ["messages"] = n,
            ["customer"] = c is null ? null : new JsonObject { ["id"] = c.Id, ["company"] = c.Company },
        };
    }

    static Ticket FindTicket(Req r) =>
        r.S.Tickets.Find(Validate.PathInt("ticket_id", r.Route("ticket_id"))) ?? throw new ApiError(404, "no such ticket");

    static JsonObject TicketDetail(Db s, Ticket t)
    {
        var authors = new Dictionary<int, string?>();
        var thread = new JsonArray();
        foreach (var m in s.TicketMessages.Where(m => m.TicketId == t.Id).OrderBy(m => m.Id).ToList())
        {
            if (m.AuthorId is int aid && !authors.ContainsKey(aid))
            {
                var u = s.Users.Find(aid);
                authors[aid] = u is null ? null : u.Name is { Length: > 0 } nm ? nm : u.Email;
            }
            thread.Add(new JsonObject
            {
                ["id"] = m.Id, ["kind"] = m.Kind, ["body"] = m.Body, ["at"] = Iso(m.CreatedAt), ["emailed"] = m.Emailed == true,
                ["author"] = m.AuthorId is int a ? authors[a] : null,
            });
        }
        var order = t.OrderId is int oid ? s.Orders.Find(oid) : null;
        var customer = t.CustomerId is int cid ? s.Customers.Find(cid) : null;
        var acct = t.AccountId is int acid ? s.Accounts.Find(acid) : null;
        var system = t.SystemId is int sid ? s.Systems.Find(sid) : null;
        var o = TicketRow(s, t, customer is null ? [] : new() { [customer.Id] = customer });
        o["phone"] = t.Phone;
        o["body"] = t.Body;
        o["order"] = order is null ? null : new JsonObject { ["id"] = order.Id, ["systems"] = order.Systems };
        o["account"] = acct is null ? null : new JsonObject { ["id"] = acct.Id, ["name"] = acct.Name };
        o["system"] = system is null ? null : new JsonObject { ["id"] = system.Id, ["name"] = system.Name, ["site_id"] = system.SiteId };
        o["thread"] = thread;
        return o;
    }

    /// <summary>Counts for the Office header.</summary>
    object? OfficeSummary(Req r)
    {
        StaffOnly(r.Who);
        var inbox = r.S.Tickets.GroupBy(t => t.Status).Select(g => new { g.Key, N = g.Count() }).ToDictionary(x => x.Key, x => x.N);
        var cust = r.S.Customers.GroupBy(c => c.Status).Select(g => new { g.Key, N = g.Count() }).ToDictionary(x => x.Key, x => x.N);
        var i = new JsonObject();
        foreach (var k in TicketStatuses) i[k] = inbox.GetValueOrDefault(k);
        var c = new JsonObject();
        foreach (var k in CustomerStatuses) c[k] = cust.GetValueOrDefault(k);
        return new JsonObject { ["inbox"] = i, ["customers"] = c };
    }

    /// <summary>Tickets, most recently active first. "open" = everything not closed.</summary>
    object? Inbox(Req r)
    {
        StaffOnly(r.Who);
        var status = Validate.Query(r.Query("status"), Field.Choice("status", ["open", "closed", "all"]), JsonValue.Create("open")).GetValue<string>();
        var kind = Validate.Query(r.Query("kind"), Field.Choice("kind", ["order", "contact", "support", "all"]), JsonValue.Create("all")).GetValue<string>();
        IQueryable<Ticket> q = r.S.Tickets;
        if (status == "open") q = q.Where(t => t.Status != "closed");
        else if (status == "closed") q = q.Where(t => t.Status == "closed");
        if (kind != "all") q = q.Where(t => t.Kind == kind);
        var rows = q.OrderByDescending(t => t.UpdatedAt).ThenByDescending(t => t.Id).Take(500).ToList();
        var ids = rows.Where(t => t.CustomerId != null).Select(t => t.CustomerId!.Value).ToHashSet();
        var customers = r.S.Customers.Where(c => ids.Contains(c.Id)).ToDictionary(c => c.Id);
        return new JsonArray(rows.Select(t => (JsonNode?)TicketRow(r.S, t, customers)).ToArray());
    }

    object? GetTicket(Req r)
    {
        StaffOnly(r.Who);
        var t = FindTicket(r);
        if (t.Status == "new")                                      // opened by staff: no longer new
        {
            t.Status = "open";
            r.S.SaveChanges();
        }
        return TicketDetail(r.S, t);
    }

    object? UpdateTicket(Req r)
    {
        StaffOnly(r.Who);
        var b = Body(r, Field.Choice("status", TicketStatuses), Field.Int("customer_id", ge: 0));
        var t = FindTicket(r);
        if (b.Str("status") is string st) t.Status = st;
        if (b.IsSet("customer_id"))
        {
            if (b.Int("customer_id") is int cid && cid != 0)
            {
                if (r.S.Customers.Find(cid) is null) throw new ApiError(404, "no such customer");
                t.CustomerId = cid;
            }
            else t.CustomerId = null;
        }
        t.UpdatedAt = Db.UtcNow();
        r.S.SaveChanges();
        return TicketDetail(r.S, t);
    }

    /// <summary>A reply (emailed to the sender, and shown to a contractor in the app) or an internal note.</summary>
    object? AddTicketMessage(Req r)
    {
        var who = r.Who;
        StaffOnly(who);
        var b = Body(r, Field.Choice("kind", ["reply", "note"], required: true), Field.Str("body", 4000, required: true, minLength: 1));
        var t = FindTicket(r);
        var text = b.Str("body")!.Trim();
        if (text.Length == 0) throw new ApiError(422, "Write a message first.");
        bool? emailed = null;
        if (b.Str("kind") == "reply")
        {
            emailed = SendInvite(t.Email, $"Re: {t.Subject} [Fullscope #{t.Id}]", $"{text}\n\n--\nFullscope\n{_settings.AppUrl}") ? true : null;
            if (t.Status is "new" or "open") t.Status = "waiting";
        }
        r.S.TicketMessages.Add(new TicketMessage { TicketId = t.Id, Kind = b.Str("kind")!, Body = text, AuthorId = who.UserId, Emailed = emailed });
        t.UpdatedAt = Db.UtcNow();
        r.S.SaveChanges();
        return TicketDetail(r.S, t);
    }

    /// <summary>Make a customer from the ticket's sender (an order request, usually) and link the two.</summary>
    object? TicketToCustomer(Req r)
    {
        StaffOnly(r.Who);
        var t = FindTicket(r);
        if (t.CustomerId is not null) throw new ApiError(409, "This ticket already has a customer.");
        var c = new Customer
        {
            Company = !string.IsNullOrEmpty(t.Company) ? t.Company : !string.IsNullOrEmpty(t.Name) ? t.Name : t.Email,
            Contact = t.Name, Email = t.Email, Phone = t.Phone, Status = "lead",
        };
        if (t.AccountId is int aid && CustomerForAccount(r.S, aid) is null) c.AccountId = aid;
        r.S.Customers.Add(c);
        r.S.SaveChanges();
        t.CustomerId = c.Id;
        t.UpdatedAt = Db.UtcNow();
        r.S.SaveChanges();
        return TicketDetail(r.S, t);
    }

    // ---------- staff: customers ----------

    static JsonObject CustomerRow(Db s, Customer c)
    {
        int systems = c.AccountId is int aid ? s.Systems.Count(x => x.AccountId == aid) : 0;
        int open = s.Tickets.Count(t => t.CustomerId == c.Id && t.Status != "closed");
        return new JsonObject
        {
            ["id"] = c.Id, ["company"] = c.Company, ["contact"] = c.Contact, ["email"] = c.Email, ["phone"] = c.Phone,
            ["status"] = c.Status, ["created_at"] = Iso(c.CreatedAt), ["account_id"] = c.AccountId, ["systems"] = systems, ["open_tickets"] = open,
        };
    }

    static Customer FindCustomer(Req r) =>
        r.S.Customers.Find(Validate.PathInt("customer_id", r.Route("customer_id"))) ?? throw new ApiError(404, "no such customer");

    JsonObject CustomerDetail(Db s, Customer c)
    {
        var o = CustomerRow(s, c);
        o["address"] = c.Address;
        o["notes"] = c.Notes;
        o["account"] = null;
        var people = new JsonArray();
        var invites = new JsonArray();
        var systems = new JsonArray();
        var now = Db.UtcNow();
        if (c.AccountId is int aid)
        {
            var acct = s.Accounts.Find(aid)!;
            o["account"] = new JsonObject { ["id"] = acct.Id, ["name"] = acct.Name, ["email"] = acct.Email, ["created_at"] = Iso(acct.CreatedAt) };
            foreach (var u in s.Users.Where(u => u.AccountId == aid).OrderBy(u => u.Email).ToList()) people.Add(TeammateView(u));
            foreach (var i in s.Invites.Where(i => i.AccountId == aid && i.Role == "contractor" && i.AcceptedAt == null)
                               .OrderByDescending(i => i.CreatedAt).ToList()) invites.Add(InviteView(i));
            foreach (var system in s.Systems.Where(x => x.AccountId == aid).OrderBy(x => x.Name).ToList())
            {
                var last = s.Snapshots.Where(x => x.SystemId == system.Id).OrderByDescending(x => x.Time).Select(x => (DateTime?)x.Time).FirstOrDefault();
                var devices = s.Devices.Where(d => d.SystemId == system.Id).ToList();
                int online = devices.Count(d => d.Connected && d.LastSeen is DateTime ls && Seconds(now - ls) <= _stale);
                int homeowners = s.SystemMembers.Count(m => m.SystemId == system.Id);
                systems.Add(new JsonObject
                {
                    ["id"] = system.Id, ["name"] = system.Name, ["site_id"] = system.SiteId, ["last_reading"] = Iso(last),
                    ["nodes_online"] = online, ["nodes"] = devices.Count, ["homeowners"] = homeowners,
                });
            }
        }
        o["people"] = people;
        o["invites"] = invites;
        o["systems_list"] = systems;
        var tickets = s.Tickets.Where(t => t.CustomerId == c.Id).OrderByDescending(t => t.UpdatedAt).ThenByDescending(t => t.Id).ToList();
        o["tickets"] = new JsonArray(tickets.Select(t => (JsonNode?)TicketRow(s, t, new() { [c.Id] = c })).ToArray());
        return o;
    }

    static Field[] CustomerFields() =>
    [
        Field.Str("company", 200, minLength: 1), Field.Str("contact", 200), Field.Str("email", 320), Field.Str("phone", 40),
        Field.Str("address", 500), Field.Choice("status", CustomerStatuses), Field.Str("notes", 4000),
    ];

    static void ApplyCustomer(Customer c, Model b)
    {
        if (b.IsSet("company"))
        {
            var company = (b.Str("company") ?? "").Trim();
            if (company.Length == 0) throw new ApiError(422, "The company name is required.");
            c.Company = company;
        }
        if (b.IsSet("contact")) c.Contact = Clean(b.Str("contact"));
        if (b.IsSet("phone")) c.Phone = Clean(b.Str("phone"));
        if (b.IsSet("address")) c.Address = Clean(b.Str("address"));
        if (b.IsSet("notes")) c.Notes = Clean(b.Str("notes"));
        if (b.IsSet("email")) c.Email = Clean(b.Str("email")) is null ? null : CleanEmail(b.Str("email")!);
        if (b.IsSet("status") && b.Str("status") is string st) c.Status = st;
    }

    object? ListCustomers(Req r)
    {
        StaffOnly(r.Who);
        var rows = r.S.Customers.OrderBy(c => c.Company).ThenBy(c => c.Id).ToList();
        return new JsonArray(rows.Select(c => (JsonNode?)CustomerRow(r.S, c)).ToArray());
    }

    object? AddCustomer(Req r)
    {
        StaffOnly(r.Who);
        var b = Body(r, CustomerFields());
        if ((b.Str("company") ?? "").Trim().Length == 0) throw new ApiError(422, "The company name is required.");
        var c = new Customer { Company = b.Str("company")!.Trim(), Status = "lead" };
        ApplyCustomer(c, b);
        r.S.Customers.Add(c);
        r.S.SaveChanges();
        return CustomerDetail(r.S, c);
    }

    object? GetCustomer(Req r)
    {
        StaffOnly(r.Who);
        return CustomerDetail(r.S, FindCustomer(r));
    }

    object? UpdateCustomer(Req r)
    {
        StaffOnly(r.Who);
        var b = Body(r, CustomerFields());
        var c = FindCustomer(r);
        ApplyCustomer(c, b);
        r.S.SaveChanges();
        return CustomerDetail(r.S, c);
    }

    /// <summary>Give the customer their Fullscope account and email its first admin an invite.</summary>
    object? MakeCustomerAccount(Req r)
    {
        var who = r.Who;
        StaffOnly(who);
        var b = Body(r, Field.Str("email", 320, required: true));
        var c = FindCustomer(r);
        if (c.AccountId is not null) throw new ApiError(409, "This customer already has an account.");
        var email = CleanEmail(b.Str("email")!);
        if (r.S.Users.Any(u => u.Email == email)) throw new ApiError(409, "That email already has a sign-in.");
        if (r.S.Accounts.Any(a => a.Email == email)) throw new ApiError(409, "That email already belongs to an account.");
        var acct = new Account { Name = c.Company, Email = email };
        r.S.Accounts.Add(acct);
        r.S.SaveChanges();
        c.AccountId = acct.Id;
        if (c.Status == "lead") c.Status = "active";
        if (string.IsNullOrEmpty(c.Email)) c.Email = email;
        foreach (var t in r.S.Tickets.Where(t => t.CustomerId == c.Id && t.AccountId == null).ToList()) t.AccountId = acct.Id;
        r.S.SaveChanges();
        var (_, url) = MakeInvite(r.S, who, email, "contractor", accountId: acct.Id, admin: true);
        bool emailed = SendInvite(email, $"Your Fullscope account for {c.Company}",
                                  $"Welcome to Fullscope. Your account for {c.Company} is ready.\n\n" +
                                  $"Choose a password here (the link works for {InviteDays} days):\n{url}\n\n" +
                                  "You'll be the account's admin: you can invite your technicians from the Account page.");
        var o = CustomerDetail(r.S, c);
        o["invite"] = new JsonObject { ["url"] = url, ["emailed"] = emailed, ["email"] = email };
        return o;
    }
}
