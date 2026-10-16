using MailKit.Net.Smtp;
using MailKit.Security;
using Microsoft.Extensions.Logging;
using MimeKit;

namespace Fullscope.Cloud.Ingest;

/// <summary>Something that sends plain-text email; Send returns whether it went out.</summary>
public interface IMailer
{
    bool Enabled { get; }
    bool Send(string to, string subject, string body);
}

/// <summary>Sends plain-text email through SMTP_HOST (587 STARTTLS, 465 SSL). Disabled when it is not set.</summary>
public sealed class SmtpMailer(Settings settings, ILogger log) : IMailer
{
    public string Host => settings.SmtpHost;
    string Sender => settings.SmtpFrom.Length > 0 ? settings.SmtpFrom : settings.SmtpUser;

    public bool Enabled => Host.Length > 0 && Sender.Length > 0;

    public bool Send(string to, string subject, string body)
    {
        if (!Enabled)
        {
            log.LogInformation("email off (no SMTP_HOST): {Subject}", subject);
            return false;
        }
        var msg = new MimeMessage();
        msg.From.Add(MailboxAddress.Parse(Sender));
        msg.To.Add(MailboxAddress.Parse(to));
        msg.Subject = subject;
        msg.Body = new TextPart("plain") { Text = body };
        try
        {
            using var smtp = new SmtpClient { Timeout = 20_000 };
            smtp.Connect(Host, settings.SmtpPort, settings.SmtpPort == 465 ? SecureSocketOptions.SslOnConnect : SecureSocketOptions.StartTls);
            if (settings.SmtpUser.Length > 0) smtp.Authenticate(settings.SmtpUser, settings.SmtpPass);
            smtp.Send(msg);
            smtp.Disconnect(true);
        }
        catch (Exception e) when (e is IOException or System.Net.Sockets.SocketException or MailKit.ProtocolException
                                       or MailKit.Net.Smtp.SmtpCommandException or MailKit.Security.AuthenticationException
                                       or MailKit.Security.SslHandshakeException or TimeoutException or FormatException)
        {
            log.LogWarning("email to {To} failed: {Error}", to, e.Message);
            return false;
        }
        log.LogInformation("emailed {To}: {Subject}", to, subject);
        return true;
    }
}
