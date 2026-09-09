using System.Net.Http.Headers;
using System.Text.Json;
using ETS2ModManager.Application;
using ETS2ModManager.Contracts;

namespace ETS2ModManager.Infrastructure.Workshop;

public sealed class SteamWorkshopMetadataService(HttpClient? client = null) : IWorkshopMetadataService
{
    private readonly HttpClient _http = client ?? new HttpClient();

    public async Task<IReadOnlyList<WorkshopMetadata>> FetchAsync(IEnumerable<string> workshopIds, CancellationToken cancellationToken)
    {
        var ids = workshopIds.Where(x => !string.IsNullOrWhiteSpace(x) && x.All(char.IsDigit)).Distinct().Take(50).ToArray();
        if (ids.Length == 0) return [];
        using var form = new FormUrlEncodedContent(new[] { new KeyValuePair<string, string>("itemcount", ids.Length.ToString()) }.Concat(ids.Select((id, i) => new KeyValuePair<string, string>($"publishedfileids[{i}]", id))));
        using var request = new HttpRequestMessage(HttpMethod.Post, "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/") { Content = form };
        request.Headers.UserAgent.Add(new ProductInfoHeaderValue("ETS2ModManager", "1.0"));
        try
        {
            using var response = await _http.SendAsync(request, cancellationToken); response.EnsureSuccessStatusCode();
            using var document = JsonDocument.Parse(await response.Content.ReadAsStringAsync(cancellationToken));
            if (!document.RootElement.TryGetProperty("response", out var responseNode) || !responseNode.TryGetProperty("publishedfiledetails", out var rows)) return [];
            return rows.EnumerateArray().Where(x => x.TryGetProperty("result", out var result) && result.GetInt32() == 1).Select(x => new WorkshopMetadata(x.GetProperty("publishedfileid").GetString() ?? "", x.GetProperty("title").GetString() ?? "", x.TryGetProperty("preview_url", out var preview) ? preview.GetString() : null, x.TryGetProperty("description", out var description) ? description.GetString() : null)).ToArray();
        }
        catch (OperationCanceledException) { throw; }
        catch { return []; }
    }
}
