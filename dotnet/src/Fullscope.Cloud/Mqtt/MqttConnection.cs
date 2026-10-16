using System.Buffers;
using System.Text;
using Microsoft.Extensions.Logging;
using MQTTnet;
using MQTTnet.Protocol;

namespace Fullscope.Cloud.Mqtt;

/// <summary>
/// One MQTT client that keeps itself connected (retrying every 1 s up to 30 s, like paho's
/// reconnect_delay_set(1, 30)), resubscribes after each connect and hands messages to a handler.
/// </summary>
public sealed class MqttConnection : IAsyncDisposable
{
    readonly Settings _settings;
    readonly ILogger _log;
    readonly IMqttClient _client;
    readonly string[] _topics;
    readonly CancellationTokenSource _stop = new();
    readonly TaskCompletionSource _firstConnect = new(TaskCreationOptions.RunContinuationsAsynchronously);
    Task? _loop;

    /// <summary>Called for each message, on the MQTT client's receive task.</summary>
    public Action<string, byte[]>? OnMessage { get; set; }

    public bool IsConnected => _client.IsConnected;

    /// <summary>Last-will message (published retained by the broker if this client drops).</summary>
    public (string Topic, string Payload)? Will { get; init; }

    public MqttConnection(Settings settings, string clientIdPrefix, ILogger log, params string[] subscribe)
    {
        _settings = settings;
        _log = log;
        _topics = subscribe;
        _client = new MqttClientFactory().CreateMqttClient();
        ClientId = $"{clientIdPrefix}-{Random.Shared.Next(1000, 10000)}";
        _client.ApplicationMessageReceivedAsync += e =>
        {
            try
            {
                OnMessage?.Invoke(e.ApplicationMessage.Topic, e.ApplicationMessage.Payload.ToArray());
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "failed to store {Topic}", e.ApplicationMessage.Topic);
            }
            return Task.CompletedTask;
        };
    }

    public string ClientId { get; }

    public void Start() => _loop ??= Task.Run(() => KeepConnected(_stop.Token));

    async Task KeepConnected(CancellationToken stop)
    {
        var delay = TimeSpan.FromSeconds(1);
        while (!stop.IsCancellationRequested)
        {
            if (!_client.IsConnected)
            {
                try
                {
                    var b = new MqttClientOptionsBuilder().WithTcpServer(_settings.MqttHost, _settings.MqttPort)
                        .WithClientId(ClientId).WithKeepAlivePeriod(TimeSpan.FromSeconds(30)).WithCleanSession();
                    if (_settings.MqttUser.Length > 0) b = b.WithCredentials(_settings.MqttUser, _settings.MqttPass);
                    if (Will is var (willTopic, willPayload))
                        b = b.WithWillTopic(willTopic).WithWillPayload(Encoding.UTF8.GetBytes(willPayload)).WithWillRetain()
                             .WithWillQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce);
                    var result = await _client.ConnectAsync(b.Build(), stop);
                    if (result.ResultCode != MqttClientConnectResultCode.Success)
                        throw new InvalidOperationException($"connect refused: {result.ResultCode}");
                    _log.LogInformation("connected to {Host}:{Port}", _settings.MqttHost, _settings.MqttPort);
                    if (_topics.Length > 0)
                    {
                        var sub = new MqttClientSubscribeOptionsBuilder();
                        foreach (var t in _topics) sub = sub.WithTopicFilter(t, MqttQualityOfServiceLevel.AtLeastOnce);
                        await _client.SubscribeAsync(sub.Build(), stop);
                    }
                    delay = TimeSpan.FromSeconds(1);
                    _firstConnect.TrySetResult();
                }
                catch (Exception e) when (!stop.IsCancellationRequested)
                {
                    _log.LogWarning("MQTT {Host}:{Port}: {Error}; retrying in {Delay} s", _settings.MqttHost, _settings.MqttPort,
                                    e.Message, delay.TotalSeconds);
                    try { await Task.Delay(delay, stop); } catch (OperationCanceledException) { break; }
                    delay = TimeSpan.FromSeconds(Math.Min(30, delay.TotalSeconds * 2));
                    continue;
                }
            }
            try { await Task.Delay(1000, stop); } catch (OperationCanceledException) { break; }
        }
    }

    /// <summary>Publish at QoS 1; waits up to `timeout` for a connection. False if not sent.</summary>
    public bool Publish(string topic, string payload, bool retain, TimeSpan? timeout = null)
    {
        Start();
        var wait = timeout ?? TimeSpan.FromSeconds(5);
        var until = DateTime.UtcNow + wait;
        while (!_client.IsConnected && DateTime.UtcNow < until) Thread.Sleep(50);
        if (!_client.IsConnected) return false;
        try
        {
            var msg = new MqttApplicationMessageBuilder().WithTopic(topic).WithPayload(Encoding.UTF8.GetBytes(payload))
                .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce).WithRetainFlag(retain).Build();
            var r = _client.PublishAsync(msg).GetAwaiter().GetResult();
            return r.IsSuccess;
        }
        catch (Exception e)
        {
            _log.LogWarning("publish to {Topic} failed: {Error}", topic, e.Message);
            return false;
        }
    }

    public async ValueTask DisposeAsync()
    {
        _stop.Cancel();
        if (_loop is not null) await _loop;
        if (_client.IsConnected) await _client.DisconnectAsync();
        _client.Dispose();
    }
}
