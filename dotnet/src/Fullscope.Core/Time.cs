namespace Fullscope.Core;

/// <summary>Epoch seconds and UTC DateTimes, rounded to microseconds as Python's datetime is.</summary>
public static class Time
{
    public static readonly DateTime Epoch = new(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc);

    /// <summary>datetime.fromtimestamp(t, timezone.utc): microseconds rounded half to even.</summary>
    public static DateTime FromEpoch(double t)
    {
        double secs = Math.Floor(t);
        long us = (long)Math.Round((t - secs) * 1e6, MidpointRounding.ToEven);
        return Epoch.AddTicks(((long)secs * 1_000_000 + us) * 10);
    }

    /// <summary>datetime.timestamp(): microseconds / 1e6, correctly rounded.</summary>
    public static double ToEpoch(DateTime d)
    {
        var utc = d.Kind == DateTimeKind.Local ? d.ToUniversalTime() : d;
        long us = (utc.Ticks - Epoch.Ticks) / 10;
        return us / 1e6;
    }

    public static double Now() => ToEpoch(DateTime.UtcNow);

    /// <summary>A UTC DateTime truncated to whole microseconds (what the database keeps).</summary>
    public static DateTime Micro(DateTime d) => new(d.Ticks - d.Ticks % 10, DateTimeKind.Utc);
}
