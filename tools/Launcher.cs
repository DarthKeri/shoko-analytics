using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
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
    // Windows command-line quoting: preserve spaces, quotes and trailing slashes.
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

    [STAThread]
    private static int Main(string[] arguments)
    {
        try
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string interpreter = Path.Combine(root, "runtime", "pythonw.exe");
            string entry = Path.Combine(root, "app", "main.py");
            if (!File.Exists(interpreter) || !File.Exists(entry))
            {
                MessageBox.Show("Файлы приложения не найдены. Сохраните всю папку ShokoAnalytics рядом с EXE и распакуйте её из проверенного архива.", "Shoko Analytics", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
            // Run the unmodified, separately shipped Python interpreter and source.
            // No extraction, elevation, downloads, AV settings, or self-restoration.
            StringBuilder command = new StringBuilder("-I -B ");
            command.Append(Quote(entry));
            foreach (string argument in arguments) command.Append(" ").Append(Quote(argument));
            ProcessStartInfo start = new ProcessStartInfo(interpreter, command.ToString());
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.WindowStyle = ProcessWindowStyle.Hidden;
            using (Process process = Process.Start(start))
            {
                process.WaitForExit();
                if (process.ExitCode != 0)
                    MessageBox.Show("Приложение завершилось с ошибкой. Проверьте журнал приложения и события защиты Windows. Настройки и резервные копии находятся в выбранной рабочей папке.", "Shoko Analytics", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
            MessageBox.Show("Не удалось запустить Shoko Analytics. Проверьте доступность файлов и права на папку приложения.\n\n" + error.Message, "Shoko Analytics", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
