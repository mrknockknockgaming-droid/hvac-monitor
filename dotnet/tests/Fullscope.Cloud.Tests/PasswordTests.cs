using System.Text;
using Fullscope.Cloud.Api;

namespace Fullscope.Cloud.Tests;

public class PasswordTests
{
    [Fact]
    public void ScryptMatchesRfc7914()
    {
        // RFC 7914 section 12, second vector.
        var got = Passwords.Scrypt(Encoding.UTF8.GetBytes("password"), Encoding.UTF8.GetBytes("NaCl"), 1024, 8, 16, 64);
        Assert.Equal("fdbabe1c9d3472007856e7190d01e9fe7c6ad7cbc8237830e77376634b3731622eaf30d92e22a3886ff109279d9830dac727afb94a83ee6d8360cbdfa2cc0640",
                     Convert.ToHexStringLower(got));
    }

    [Fact]
    public void ScryptMatchesPythonHashlib()
    {
        // hashlib.scrypt("Pässwörd ☃ 123".encode(), salt=b"NaCl", n=1024, r=8, p=16, dklen=64)
        var got = Passwords.Scrypt(Encoding.UTF8.GetBytes("Pässwörd ☃ 123"), Encoding.UTF8.GetBytes("NaCl"), 1024, 8, 16, 64);
        Assert.Equal("ba5ff5ff720d0d637a33e1c0ea2e53cc2bd91c16133063b70b79c54119323c628f259f02190e5fd70e2089bf84ad0fe559dfe6c9b5fd2fa3c55b982ecd01a0f8",
                     Convert.ToHexStringLower(got));
    }

    [Fact]
    public void ChecksHashesFromPython()
    {
        // auth.hash_password("correct horse battery") with salt bytes 0..15
        const string stored = "scrypt$16384$8$1$AAECAwQFBgcICQoLDA0ODw==$6W7GuoxjojaYIg83x/wEKSXUMMf+reyc6wSvA8q7ny8=";
        Assert.True(Passwords.Check("correct horse battery", stored));
        Assert.False(Passwords.Check("correct horse batterY", stored));
    }

    [Fact]
    public void OwnHashesRoundTrip()
    {
        var h = Passwords.Hash("a long enough one");
        Assert.StartsWith("scrypt$16384$8$1$", h);
        Assert.True(Passwords.Check("a long enough one", h));
        Assert.False(Passwords.Check("a long enough two", h));
        Assert.False(Passwords.Check("x", "not a hash"));
        Assert.False(Passwords.Check("x", null));
    }

    [Theory]
    [InlineData("short", true)]
    [InlineData(" leading space ok?", true)]
    [InlineData("☃☃☃☃☃☃☃☃☃☃", false)]   // ten code points
    [InlineData("ten chars!", false)]
    public void Problem(string pw, bool bad) => Assert.Equal(bad, Passwords.Problem(pw) is not null);
}
