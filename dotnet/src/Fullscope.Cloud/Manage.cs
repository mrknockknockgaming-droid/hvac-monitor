using System.Globalization;
using System.Text;
using Fullscope.Cloud.Api;
using Fullscope.Cloud.Data;
using Fullscope.Cloud.Ingest;
using Fullscope.Core;
using Microsoft.Extensions.Logging;

namespace Fullscope.Cloud;

/// <summary>
/// Admin commands for the cloud database (manage.py).
///
///   fullscope manage init-db
///   fullscope manage create-account "Tyler" tyler@example.com
///   fullscope manage create-key 1                      (prints the API key once)
///   fullscope manage create-system 1 "Home" home --refrigerant R-410A --atm-psia 14.0
///   fullscope manage list
///   fullscope manage set-email 1 you@gmail.com        (where alert emails go)
///   fullscope manage backup                            (copy the database into BACKUP_DIR, keeps 7)
///   fullscope manage prune                             (thin data older than 30 days, drop older than 365)
///   fullscope manage test-email 1                      (needs SMTP_* in .env)
///   fullscope manage create-user you@gmail.com --name "Tyler" --account 1        (contractor; asks for a password)
///   fullscope manage create-user owner@example.com --role homeowner --system 1   (sees system 1 only)
///   fullscope manage set-admin you@gmail.com          (fleet page + team; --off makes them a technician again)
///   fullscope manage set-password you@gmail.com
///   fullscope manage reset-2fa you@gmail.com          (turns two-step sign-in off, for a lost phone)
///   fullscope manage check                             (is everything working? nodes, readings, alerts, email, backups)
/// </summary>
public static class Manage
{
    sealed class Exit(string message, int code = 1) : Exception(message)
    {
        public int Code { get; } = code;
    }

    /// <summary>Parsed arguments: positionals in order, options by name (repeatable ones collect).</summary>
    sealed class Args
    {
        public readonly List<string> Pos = [];
        public readonly Dictionary<string, List<string>> Opts = [];

        public static Args Parse(string[] argv, string[] flags, string[] valued)
        {
            var a = new Args();
            for (int i = 0; i < argv.Length; i++)
            {
                var x = argv[i];
                if (x.StartsWith("--", StringComparison.Ordinal) && x.Length > 2)
                {
                    string name = x, value = "";
                    int eq = x.IndexOf('=');
                    if (eq > 0) (name, value) = (x[..eq], x[(eq + 1)..]);
                    if (flags.Contains(name)) value = "true";
                    else if (valued.Contains(name))
                    {
                        if (eq < 0)
                        {
                            if (i + 1 >= argv.Length) throw new Exit($"argument {name}: expected one argument", 2);
                            value = argv[++i];
                        }
                    }
                    else throw new Exit($"unrecognized arguments: {x}", 2);
                    if (!a.Opts.TryGetValue(name, out var list)) a.Opts[name] = list = [];
                    list.Add(value);
                }
                else a.Pos.Add(x);
            }
            return a;
        }

        public string? Opt(string name) => Opts.TryGetValue(name, out var v) ? v[^1] : null;
        public bool Flag(string name) => Opts.ContainsKey(name);
        public List<string> All(string name) => Opts.TryGetValue(name, out var v) ? v : [];

        public void Need(int n, string usage)
        {
            if (Pos.Count != n) throw new Exit("usage: fullscope manage " + usage, 2);
        }
    }

    public static int Run(Settings settings, string[] argv, ILoggerFactory logs)
    {
        try
        {
            return Execute(settings, argv, logs);
        }
        catch (Exit e)
        {
            if (e.Message.Length > 0) Console.Error.WriteLine(e.Message);
            return e.Code;
        }
    }

    static int ParseInt(string s, string what) =>
        int.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out var v) ? v : throw new Exit($"argument {what}: invalid int value: '{s}'", 2);

    static int Execute(Settings settings, string[] argv, ILoggerFactory logs)
    {
        var cmd = argv.FirstOrDefault();
        var rest = argv.Length > 0 ? argv[1..] : [];
        string[] commands = ["init-db", "create-account", "create-key", "create-system", "list", "set-email", "backup", "prune",
                             "create-user", "set-admin", "set-password", "reset-2fa", "test-email", "orders", "check"];
        if (cmd is null or "-h" or "--help" || !commands.Contains(cmd))
        {
            Console.Error.WriteLine("usage: fullscope manage {" + string.Join(",", commands) + "} ...");
            return cmd is "-h" or "--help" ? 0 : 2;
        }
        var a = Args.Parse(rest, ["--no-heat-pump", "--no-password", "--admin", "--off"], ["--refrigerant", "--atm-psia", "--ob", "--name", "--role", "--account", "--system"]);
        var log = logs.CreateLogger("manage");
        var sessions = DbFactory.FromUrl(settings.DatabaseUrl).Init(settings.KeepDays);

        if (cmd == "backup")
        {
            a.Need(0, "backup");
            var path = Maintenance.Backup(settings.DatabaseUrl, settings.BackupDir, settings.BackupKeep, log);
            if (path is null) throw new Exit("backup skipped: the database is not a SQLite file (use pg_dump)");
            return 0;
        }
        if (cmd == "prune")
        {
            a.Need(0, "prune");
            var st = Maintenance.Prune(sessions, settings.FullDetailDays, settings.KeepDays, log: log);
            Console.WriteLine($"{{'snapshots_removed': {st.SnapshotsRemoved}, 'telemetry_removed': {st.TelemetryRemoved}, 'expired': {st.Expired}}}");
            return 0;
        }
        if (cmd == "check")
        {
            a.Need(0, "check");
            using var cs = sessions.Open();
            var found = Check.Run(cs, settings, emailOn: new SmtpMailer(settings, log).Enabled);
            foreach (var (level, text) in found) Console.WriteLine($"{level,-5}{text}");
            return found.Any(f => f.Level == "FAIL") ? 1 : 0;
        }
        string? password = null;
        if (cmd == "set-password" || (cmd == "create-user" && !a.Flag("--no-password")))
        {
            a.Need(1, cmd + " <email>");
            password = AskPassword();
        }

        using var s = sessions.Open();
        using var tx = s.Database.BeginTransaction();
        switch (cmd)
        {
            case "init-db":
                a.Need(0, "init-db");
                Console.WriteLine("database ready: " + HidePassword(settings.DatabaseUrl));
                break;
            case "create-account":
            {
                a.Need(2, "create-account <name> <email>");
                var acct = new Account { Name = a.Pos[0], Email = a.Pos[1] };
                s.Accounts.Add(acct);
                s.SaveChanges();
                Console.WriteLine($"account {acct.Id}: {acct.Name} <{acct.Email}>");
                break;
            }
            case "create-key":
            {
                a.Need(1, "create-key <account_id>");
                int id = ParseInt(a.Pos[0], "account_id");
                if (s.Accounts.Find(id) is null) throw new Exit($"no account {id}");
                var (row, key) = ApiKey.New(id);
                s.ApiKeys.Add(row);
                s.SaveChanges();
                Console.WriteLine($"API key for account {id} (shown once, store it safely):\n{key}");
                break;
            }
            case "create-system":
            {
                a.Need(3, "create-system <account_id> <name> <site_id> [--refrigerant R] [--atm-psia P] [--no-heat-pump] [--ob cool|heat]");
                int id = ParseInt(a.Pos[0], "account_id");
                var refrigerant = a.Opt("--refrigerant") ?? "R-410A";
                if (!RefrigerantTables.Fluids.Contains(refrigerant))
                    throw new Exit($"argument --refrigerant: invalid choice: '{refrigerant}' (choose from {string.Join(", ", RefrigerantTables.Fluids.Select(f => $"'{f}'"))})", 2);
                var ob = a.Opt("--ob") ?? "cool";
                if (ob is not ("cool" or "heat")) throw new Exit($"argument --ob: invalid choice: '{ob}' (choose from 'cool', 'heat')", 2);
                double atm = 14.7;
                if (a.Opt("--atm-psia") is string atmText && !double.TryParse(atmText, NumberStyles.Float, CultureInfo.InvariantCulture, out atm))
                    throw new Exit($"argument --atm-psia: invalid float value: '{atmText}'", 2);
                if (s.Accounts.Find(id) is null) throw new Exit($"no account {id}");
                var site = a.Pos[2];
                if (s.Systems.Any(x => x.SiteId == site)) throw new Exit($"site id {Py.ReprString(site)} is already used");
                var system = new HvacSystem
                {
                    AccountId = id, Name = a.Pos[1], SiteId = site, Refrigerant = refrigerant, AtmPsia = atm,
                    HeatPump = !a.Flag("--no-heat-pump"), ObEnergized = ob,
                };
                s.Systems.Add(system);
                s.SaveChanges();
                Console.WriteLine($"system {system.Id}: {system.Name} (site {Py.ReprString(system.SiteId)}, {system.Refrigerant})");
                break;
            }
            case "list":
                a.Need(0, "list");
                foreach (var acct in s.Accounts.OrderBy(x => x.Id).ToList())
                {
                    Console.WriteLine($"account {acct.Id}: {acct.Name} <{acct.Email}>");
                    foreach (var system in s.Systems.Where(x => x.AccountId == acct.Id).OrderBy(x => x.Id).ToList())
                    {
                        var nodes = s.Devices.Where(d => d.SystemId == system.Id).Select(d => d.Node).ToList().Order(StringComparer.Ordinal).ToList();
                        Console.WriteLine($"  system {system.Id}: {system.Name} (site {Py.ReprString(system.SiteId)}, {system.Refrigerant}) - " +
                                          (nodes.Count > 0 ? string.Join(", ", nodes) : "no nodes yet"));
                    }
                }
                foreach (var user in s.Users.OrderBy(x => x.Id).ToList())
                {
                    var where = user.Role == "contractor"
                        ? $"account {user.AccountId?.ToString(CultureInfo.InvariantCulture) ?? "None"}"
                        : "systems " + string.Join(", ", s.SystemMembers.Where(m => m.UserId == user.Id).Select(m => m.SystemId).ToList());
                    var shown = user.Role == "contractor" && user.Admin == true ? "admin" : user.Role;
                    Console.WriteLine($"user {user.Id}: {user.Email} ({shown}, {where})" + (user.PasswordHash is not null ? "" : " - no password"));
                }
                break;
            case "set-email":
            {
                a.Need(2, "set-email <account_id> <email>");
                int id = ParseInt(a.Pos[0], "account_id");
                var acct = s.Accounts.Find(id) ?? throw new Exit($"no account {id}");
                acct.Email = a.Pos[1];
                s.SaveChanges();
                Console.WriteLine($"account {acct.Id}: {acct.Name} <{acct.Email}>");
                break;
            }
            case "create-user":
            {
                if (a.Flag("--no-password")) a.Need(1, "create-user <email> [--name N] [--role contractor|homeowner] [--account ID] [--admin] [--system ID]... [--no-password]");
                var email = a.Pos[0].Trim().ToLowerInvariant();
                var role = a.Opt("--role") ?? "contractor";
                if (role is not ("contractor" or "homeowner")) throw new Exit($"argument --role: invalid choice: '{role}' (choose from 'contractor', 'homeowner')", 2);
                int? account = a.Opt("--account") is string acc ? ParseInt(acc, "--account") : null;
                var systems = a.All("--system").Select(x => ParseInt(x, "--system")).ToList();
                if (s.Users.Any(u => u.Email == email)) throw new Exit($"{email} already has a sign-in (use set-password to change it)");
                if (role == "contractor")
                {
                    if (account is null || s.Accounts.Find(account.Value) is null)
                        throw new Exit("a contractor needs --account <id> of an existing account (see list)");
                }
                else if (systems.Count == 0) throw new Exit("a homeowner needs at least one --system <id>");
                foreach (var sid in systems)
                    if (s.Systems.Find(sid) is null) throw new Exit($"no system {sid}");
                var user = new User
                {
                    Email = email, Name = a.Opt("--name"), Role = role, AccountId = role == "contractor" ? account : null,
                    PasswordHash = password is not null ? Passwords.Hash(password) : null,
                    Admin = a.Flag("--admin") && role == "contractor" ? true : null,
                };
                s.Users.Add(user);
                s.SaveChanges();
                foreach (var sid in systems) s.SystemMembers.Add(new SystemMember { SystemId = sid, UserId = user.Id });
                s.SaveChanges();
                Schema.EnsureAdmins(s);                   // an account's first contractor is its admin
                Console.WriteLine($"user {user.Id}: {email} ({(user.Admin == true ? "admin" : role)})" + (password is not null ? "" : " - no password yet"));
                break;
            }
            case "set-admin":
            {
                a.Need(1, "set-admin <email> [--off]");
                var email = a.Pos[0].Trim().ToLowerInvariant();
                var user = s.Users.FirstOrDefault(u => u.Email == email && u.Role == "contractor") ?? throw new Exit($"no contractor {a.Pos[0]}");
                bool off = a.Flag("--off");
                if (off && user.Admin == true
                    && !s.Users.Any(u => u.Role == "contractor" && u.AccountId == user.AccountId && u.Admin == true && u.Id != user.Id))
                    throw new Exit("the account needs at least one admin: make someone else an admin first");
                user.Admin = off ? null : true;
                s.SaveChanges();
                Console.WriteLine($"{user.Email} is now {(off ? "a technician" : "an admin")}");
                break;
            }
            case "set-password":
            {
                var email = a.Pos[0].Trim().ToLowerInvariant();
                var user = s.Users.FirstOrDefault(u => u.Email == email) ?? throw new Exit($"no user {a.Pos[0]}");
                user.PasswordHash = Passwords.Hash(password!);
                s.SaveChanges();
                Console.WriteLine($"password set for {user.Email}");
                break;
            }
            case "reset-2fa":
            {
                a.Need(1, "reset-2fa <email>");
                var email = a.Pos[0].Trim().ToLowerInvariant();
                var user = s.Users.FirstOrDefault(u => u.Email == email) ?? throw new Exit($"no user {a.Pos[0]}");
                Totp.TurnOff(user);
                s.SaveChanges();
                Console.WriteLine($"two-step sign-in is off for {user.Email}; they can turn it on again on their Account page");
                break;
            }
            case "orders":
            {
                a.Need(0, "orders");
                var rows = s.Orders.OrderByDescending(o => o.Id).ToList();
                if (rows.Count == 0) Console.WriteLine("no order requests yet");
                foreach (var o in rows)
                {
                    Console.WriteLine($"#{o.Id} {o.CreatedAt.ToString("yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture)} UTC  {o.Company}: {o.Systems} system(s)  " +
                                      $"{o.Name} <{o.Email}> {o.Phone ?? ""}".TrimEnd());
                    if (o.Notes is not null) Console.WriteLine($"    {o.Notes}");
                }
                break;
            }
            case "test-email":
            {
                a.Need(1, "test-email <account_id>");
                int id = ParseInt(a.Pos[0], "account_id");
                var acct = s.Accounts.Find(id) ?? throw new Exit($"no account {id}");
                var mailer = new SmtpMailer(settings, log);
                if (!mailer.Enabled) throw new Exit("email is off: set SMTP_HOST, SMTP_USER and SMTP_PASS in hvac-cloud/.env");
                bool ok = mailer.Send(acct.Email, "[Fullscope] Test alert email",
                                      "Alert emails from your Fullscope cloud are set up and working.");
                tx.Commit();
                if (!ok) throw new Exit($"sending to {acct.Email} failed; see the message above");
                return 0;
            }
        }
        tx.Commit();
        return 0;
    }

    static string HidePassword(string url)
    {
        int at = url.IndexOf('@'), scheme = url.IndexOf("://", StringComparison.Ordinal);
        if (at < 0 || scheme < 0) return url;
        int colon = url.IndexOf(':', scheme + 3);
        return colon > 0 && colon < at ? url[..(colon + 1)] + "***" + url[at..] : url;
    }

    /// <summary>Typed twice at a hidden prompt, so it never appears on screen or in shell history.</summary>
    static string AskPassword()
    {
        while (true)
        {
            var first = ReadHidden($"New password (at least {Passwords.MinLength} characters): ");
            if (Passwords.Problem(first) is string problem)
            {
                Console.WriteLine(problem);
                continue;
            }
            if (ReadHidden("Same password again: ") != first)
            {
                Console.WriteLine("They don't match; try again.");
                continue;
            }
            return first;
        }
    }

    static string ReadHidden(string prompt)
    {
        Console.Write(prompt);
        if (Console.IsInputRedirected)
        {
            var line = Console.ReadLine() ?? throw new Exit("", 1);
            Console.WriteLine();
            return line;
        }
        var sb = new StringBuilder();
        while (true)
        {
            var k = Console.ReadKey(intercept: true);
            if (k.Key == ConsoleKey.Enter) break;
            if (k.Key == ConsoleKey.Backspace)
            {
                if (sb.Length > 0) sb.Length--;
            }
            else if (k.Modifiers.HasFlag(ConsoleModifiers.Control) && k.Key == ConsoleKey.C) throw new Exit("", 130);
            else if (k.KeyChar != '\0') sb.Append(k.KeyChar);
        }
        Console.WriteLine();
        return sb.ToString();
    }
}
