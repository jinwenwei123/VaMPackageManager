using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("VaM Package Manager")]
[assembly: AssemblyDescription("VaM local package library manager")]
[assembly: AssemblyVersion("1.0.1.0")]
internal static class Launcher
{
    [STAThread]
    private static void Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string runtime = Path.Combine(root, "runtime", "pythonw.exe");
        string script = Path.Combine(root, "src", "main.py");
        try
        {
            if (!File.Exists(runtime) || !File.Exists(script))
                throw new FileNotFoundException("Keep the complete application folder. The EXE requires the adjacent runtime, src and web directories.");
            string arguments = "\"" + script + "\"";
            // A diagnostic flag used by the automated Windows smoke test.
            if (Array.IndexOf(args, "--no-browser") >= 0) arguments += " --no-browser";
            ProcessStartInfo start = new ProcessStartInfo(runtime, arguments);
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            Process.Start(start);
        }
        catch (Exception e)
        {
            MessageBox.Show(e.Message, "VaM Package Manager — Startup failed", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
