using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;

[assembly: AssemblyTitle("Shoko Analytics")]
[assembly: AssemblyDescription("Shoko Analytics desktop launcher")]
[assembly: AssemblyCompany("Shoko")]
[assembly: AssemblyProduct("Shoko Analytics")]
[assembly: AssemblyVersion("0.1.0.0")]
[assembly: AssemblyFileVersion("0.1.0")]
[assembly: AssemblyInformationalVersion("0.1.0")]

internal static class Launcher
{
    private const int OutputLimit = 65536;

    private static string Quote(string value)
    {
        StringBuilder text = new StringBuilder("\"");
        int slashes = 0;
        foreach (char item in value)
        {
            if (item == '\\') { slashes++; continue; }
            if (item == '"') text.Append('\\', slashes * 2 + 1);
            else text.Append('\\', slashes);
            text.Append(item);
            slashes = 0;
        }
        text.Append('\\', slashes * 2);
        text.Append('"');
        return text.ToString();
    }

    private static string Sanitize(string text)
    {
        text = text ?? "";
        string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        if (!String.IsNullOrEmpty(home))
            text = Regex.Replace(text, Regex.Escape(home), "%USERPROFILE%", RegexOptions.IgnoreCase);
        // Do not collect configuration, tokens or raw command-line arguments.
        // Mask secret-bearing diagnostic lines as a whole, including partial values.
        text = Regex.Replace(text, @"(?im)[""']?\b(client[_-]?secret|access[_-]?token|refresh[_-]?token|id[_-]?token|password|authorization|cookie|api[_-]?key|code[_-]?verifier)[""']?\s*[:=][^\r\n]*", "$1=[REDACTED]");
        text = Regex.Replace(text, @"(?i)\bBearer\s+[^\s""'<>]+", "Bearer [REDACTED]");
        text = Regex.Replace(text, @"\b(?:ya29\.|1//|GOCSPX-|gh[pousr]_)[A-Za-z0-9._~-]+", "[REDACTED]");
        text = Regex.Replace(text, @"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED]");
        text = Regex.Replace(text, @"(?i)(https?://[^\s?""'<>]+)\?[^\s""'<>]*", "$1?[REDACTED]");
        return text;
    }

    private static void Capture(StringBuilder buffer, string value)
    {
        if (value == null) return;
        lock (buffer)
        {
            buffer.AppendLine(Sanitize(value));
            if (buffer.Length > OutputLimit) buffer.Remove(0, buffer.Length - OutputLimit);
        }
    }

    private static string PrepareReport(string root)
    {
        string[] folders = {
            Path.Combine(root, "diagnostics"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ShokoAnalytics", "diagnostics"),
            Path.Combine(Path.GetTempPath(), "ShokoAnalytics", "diagnostics")
        };
        foreach (string folder in folders)
        {
            try
            {
                Directory.CreateDirectory(folder);
                string path = Path.Combine(folder, "startup-" + DateTime.Now.ToString("yyyyMMdd-HHmmss-fff") + "-" + Guid.NewGuid().ToString("N").Substring(0, 8) + ".txt");
                using (FileStream file = new FileStream(path + ".details", FileMode.CreateNew, FileAccess.Write, FileShare.ReadWrite)) { }
                return path;
            }
            catch (Exception) { }
        }
        return null;
    }

    private static string Details(string path)
    {
        if (String.IsNullOrEmpty(path)) return "";
        try
        {
            using (FileStream stream = new FileStream(path + ".details", FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            {
                if (stream.Length > 131072) stream.Seek(-131072, SeekOrigin.End);
                using (StreamReader reader = new StreamReader(stream, Encoding.UTF8)) return reader.ReadToEnd();
            }
        }
        catch (Exception error) { return "Не удалось прочитать подробности: " + error.GetType().Name; }
    }

    private static void RemoveDetails(string path)
    {
        if (String.IsNullOrEmpty(path)) return;
        try { File.Delete(path + ".details"); } catch (Exception) { }
    }

    private static string SaveFailure(string root, string report, string phase, int code, string stdout, string stderr, Exception error)
    {
        if (String.IsNullOrEmpty(report)) return null;
        StringBuilder text = new StringBuilder();
        text.AppendLine("SHOKOLAD / SHOKO ANALYTICS 0.1.0 — ОШИБКА ЗАПУСКА");
        text.AppendLine("Время: " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss zzz"));
        text.AppendLine("Этап EXE: " + phase);
        text.AppendLine("Код завершения: " + code + " (0x" + unchecked((uint)code).ToString("X8") + ")");
        text.AppendLine("Windows: " + Environment.OSVersion);
        text.AppendLine("Windows x64: " + Environment.Is64BitOperatingSystem);
        text.AppendLine(".NET CLR: " + Environment.Version);
        text.AppendLine("Папка приложения: " + root);
        foreach (string name in new string[] { "runtime/pythonw.exe", "runtime/python313.dll", "app/bootstrap.py", "app/startup_diagnostics.py", "app/main.py", "app/web/index.html" })
            text.AppendLine((File.Exists(Path.Combine(root, name)) ? "OK: " : "НЕТ ФАЙЛА: ") + name);
        if (error != null) text.AppendLine("Ошибка EXE: " + error.GetType().FullName + ": " + error.Message);
        text.AppendLine("\r\n--- Этапы Python и подробности ---");
        text.AppendLine(Details(report));
        text.AppendLine("\r\n--- stderr ---"); text.AppendLine(stderr);
        text.AppendLine("\r\n--- stdout ---"); text.AppendLine(stdout);
        text.AppendLine("\r\nНастройки и сохранённые входы в отчёт не копировались. Отчёт никуда не отправляется автоматически.");
        try
        {
            File.WriteAllText(report, Sanitize(text.ToString()), new UTF8Encoding(true));
            RemoveDetails(report);
            return report;
        }
        catch (Exception) { return null; }
    }

    private static void ShowFailure(string report, bool quiet)
    {
        if (quiet) return;
        string message = "Не удалось запустить Shoko Analytics.";
        if (report != null)
            message += "\r\n\r\nПодробности сохранены в файле:\r\n" + report + "\r\n\r\nНажмите ОК, чтобы открыть отчёт и передать его ответственному.";
        else
            message += "\r\nНе удалось сохранить отчёт. Проверьте права на папку приложения и временную папку Windows.\r\nЗапустите «Проверить запуск.cmd» рядом с EXE.";
        MessageBox.Show(message, "Shoko Analytics", MessageBoxButtons.OK, MessageBoxIcon.Error);
        if (report != null)
        {
            try
            {
                ProcessStartInfo viewer = new ProcessStartInfo("notepad.exe", Quote(report));
                viewer.UseShellExecute = false;
                Process.Start(viewer);
            }
            catch (Exception) { }
        }
    }

    [STAThread]
    private static int Main(string[] arguments)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string report = PrepareReport(root);
        string phase = "Проверка файлов приложения";
        bool quiet = Array.IndexOf(arguments, "--smoke") >= 0 || Array.IndexOf(arguments, "--ui-smoke") >= 0;
        StringBuilder stdout = new StringBuilder(), stderr = new StringBuilder();
        try
        {
            string interpreter = Path.Combine(root, "runtime", "pythonw.exe");
            string entry = Path.Combine(root, "app", "bootstrap.py");
            foreach (string path in new string[] { interpreter, entry, Path.Combine(root, "app", "main.py"), Path.Combine(root, "app", "startup_diagnostics.py") })
                if (!File.Exists(path)) throw new FileNotFoundException("Не найден файл приложения", path);
            StringBuilder command = new StringBuilder("-I -B ");
            command.Append(Quote(entry));
            foreach (string argument in arguments) command.Append(" ").Append(Quote(argument));
            ProcessStartInfo start = new ProcessStartInfo(interpreter, command.ToString());
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.WindowStyle = ProcessWindowStyle.Hidden;
            start.RedirectStandardOutput = true;
            start.RedirectStandardError = true;
            start.StandardOutputEncoding = Encoding.UTF8;
            start.StandardErrorEncoding = Encoding.UTF8;
            start.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            if (report != null) start.EnvironmentVariables["SHOKO_STARTUP_DETAILS"] = report + ".details";
            phase = "Запуск встроенного Python";
            using (Process process = new Process())
            {
                process.StartInfo = start;
                process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { Capture(stdout, e.Data); };
                process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { Capture(stderr, e.Data); };
                process.Start();
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                phase = "Работа Python / окна приложения";
                process.WaitForExit();
                int code = process.ExitCode;
                if (code != 0)
                    ShowFailure(SaveFailure(root, report, phase, code, stdout.ToString(), stderr.ToString(), null), quiet);
                else RemoveDetails(report);
                return code;
            }
        }
        catch (Exception error)
        {
            ShowFailure(SaveFailure(root, report, phase, 1, stdout.ToString(), stderr.ToString(), error), quiet);
            return 1;
        }
    }
}
