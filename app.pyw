"""Double-click to launch without a console window."""

import tkinter as tk
from tkinter import messagebox

from usage_monitor.ui import run


if __name__ == "__main__":
    try:
        run()
    except Exception:
        fallback = tk.Tk()
        fallback.withdraw()
        messagebox.showerror("Uso y límites", "No se pudo iniciar la aplicación. Ejecuta python -m usage_monitor desde la carpeta del proyecto para diagnosticar el error.")
        fallback.destroy()
