using System.Runtime.InteropServices;
using System.Text;

namespace ETS2ModManager.Infrastructure.Rust;

[StructLayout(LayoutKind.Sequential)]
internal readonly struct NativeBuffer
{
    public readonly nint Pointer;
    public readonly nuint Length;
}

[StructLayout(LayoutKind.Sequential)]
internal readonly struct NativeResult
{
    public readonly int Code;
    public readonly NativeBuffer Data;
    public readonly NativeBuffer Error;
}

internal static partial class Ets2CoreNative
{
    private const string LibraryName = "ets2_core_ffi";

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    internal static extern uint ets2_core_abi_version();

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    internal static extern NativeResult ets2_core_inspect_bytes(byte[] bytes, nuint length);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    internal static extern NativeResult ets2_core_count_packages(byte[] bytes, nuint length);

    [DllImport(LibraryName, CallingConvention = CallingConvention.Cdecl)]
    internal static extern void ets2_core_free_buffer(NativeBuffer buffer);
}

public sealed class Ets2CoreClient
{
    public const uint SupportedAbiVersion = 1;

    public string InspectBytes(ReadOnlySpan<byte> bytes) =>
        Invoke(bytes.ToArray(), Ets2CoreNative.ets2_core_inspect_bytes);

    public string CountPackages(IEnumerable<string> paths)
    {
        var bytes = Encoding.UTF8.GetBytes(string.Join('\n', paths));
        return Invoke(bytes, Ets2CoreNative.ets2_core_count_packages);
    }

    public void EnsureCompatible()
    {
        var actual = Ets2CoreNative.ets2_core_abi_version();
        if (actual != SupportedAbiVersion)
        {
            throw new InvalidOperationException(
                $"Unsupported ets2_core ABI version {actual}; expected {SupportedAbiVersion}.");
        }
    }

    private static string Invoke(byte[] input, Func<byte[], nuint, NativeResult> operation)
    {
        var result = operation(input, (nuint)input.Length);
        try
        {
            if (result.Code != 0)
            {
                throw new InvalidOperationException(ReadUtf8(result.Error));
            }
            return ReadUtf8(result.Data);
        }
        finally
        {
            Ets2CoreNative.ets2_core_free_buffer(result.Data);
            Ets2CoreNative.ets2_core_free_buffer(result.Error);
        }
    }

    private static string ReadUtf8(NativeBuffer buffer)
    {
        if (buffer.Pointer == nint.Zero || buffer.Length == 0)
        {
            return string.Empty;
        }
        if (buffer.Length > (nuint)int.MaxValue)
        {
            throw new InvalidOperationException("Native buffer exceeds managed limits.");
        }
        var bytes = new byte[(int)buffer.Length];
        Marshal.Copy(buffer.Pointer, bytes, 0, bytes.Length);
        return Encoding.UTF8.GetString(bytes);
    }
}
