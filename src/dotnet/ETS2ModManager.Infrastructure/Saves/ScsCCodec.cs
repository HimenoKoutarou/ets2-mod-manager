using System.IO.Compression;
using System.Security.Cryptography;

namespace ETS2ModManager.Infrastructure.Saves;

public static class ScsCCodec
{
    private static readonly byte[] Key = [0x2A, 0x5F, 0xCB, 0x17, 0x91, 0xD2, 0x2F, 0xB6, 0x02, 0x45, 0xB3, 0xD8, 0x36, 0x9E, 0xD0, 0xB2, 0xC2, 0x73, 0x71, 0x56, 0x3F, 0xBF, 0x1F, 0x3C, 0x9E, 0xDF, 0x6B, 0x11, 0x82, 0x5A, 0x5D, 0x0A];

    public static bool IsScsC(ReadOnlySpan<byte> bytes) => bytes.Length >= 4 && bytes[..4].SequenceEqual("ScsC"u8);

    public static byte[] Decrypt(byte[] data)
    {
        if (!IsScsC(data)) return data;
        if (data.Length < 56) throw new InvalidDataException("ScsC container header is incomplete.");
        using var aes = Aes.Create(); aes.Key = Key; aes.IV = data[36..52]; aes.Mode = CipherMode.CBC; aes.Padding = PaddingMode.PKCS7;
        var decrypted = aes.CreateDecryptor().TransformFinalBlock(data, 56, data.Length - 56);
        using var input = new MemoryStream(decrypted); using var zlib = new ZLibStream(input, CompressionMode.Decompress); using var output = new MemoryStream(); zlib.CopyTo(output);
        var expected = BitConverter.ToInt32(data, 52);
        if (expected > 0 && output.Length != expected) throw new InvalidDataException($"ScsC payload length mismatch: {output.Length} != {expected}.");
        return output.ToArray();
    }

    public static byte[] Encrypt(byte[] plain)
    {
        using var compressed = new MemoryStream();
        using (var zlib = new ZLibStream(compressed, CompressionLevel.SmallestSize, leaveOpen: true)) zlib.Write(plain);
        using var aes = Aes.Create(); aes.Key = Key; aes.GenerateIV(); aes.Mode = CipherMode.CBC; aes.Padding = PaddingMode.PKCS7;
        var encrypted = aes.CreateEncryptor().TransformFinalBlock(compressed.ToArray(), 0, (int)compressed.Length);
        using var output = new MemoryStream(); output.Write("ScsC"u8); output.Write(new byte[32]); output.Write(aes.IV); output.Write(BitConverter.GetBytes(plain.Length)); output.Write(encrypted); return output.ToArray();
    }
}
