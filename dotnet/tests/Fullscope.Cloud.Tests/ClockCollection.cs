namespace Fullscope.Cloud.Tests;

/// <summary>Tests that set the shared clock or time zones (Time.Clock, Time.LocalZone, Alerts.LocalZone) run one at a time.</summary>
[CollectionDefinition("clock", DisableParallelization = true)]
public sealed class ClockCollection;
