import re
import tkinter as tk
from tkinter import ttk
import matplotlib

matplotlib.use("TkAgg")

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# --------------------------------------------------------------------
# 1) Original compand filter string:
# --------------------------------------------------------------------
# compand_filter_original = (
#     'compand='
#     'attacks=0:'
#     'decays=0.3:'
#     'soft-knee=6:'
#     'points='
#     '-110/-110|'
#     '-100/-105|'
#     '-90/-95|'
#     '-80/-90|'
#     '-75/-85|'
#     '-70/-65|'
#     '-60/-55|'
#     '-50/-40|'
#     '-45/-35|'
#     '-35/-28|'
#     '-30/-24|'
#     '-27/-16|'
#     '-20/-14|'
#     '-10/-12|'
#     '-5/-10|'
#     '0/-10|'
#     '10/-10|'
#     '20/-10'
#     ':gain=4'
# )
compand_filter_original = (
    'compand=attacks=0:decays=0.3:soft-knee=6:points=-110.00/-110.00|-100.00/-105.00|-88.88/-98.04|-80.00/-90.00|-75.00/-85.00|-63.89/-68.04|-51.56/-51.73|-42.14/-39.32|-34.35/-27.25|-31.43/-22.64|-27.54/-18.38|-24.29/-15.90|-20.07/-13.77|-13.91/-12.51|-6.12/-11.25|1.02/-10.71|10.00/-10.00|20.00/-10.00:gain=0'
)


# --------------------------------------------------------------------
# 2) Parse the string to get the points (dB_in, dB_out).
#    We'll store them as a list of (x, y).
# --------------------------------------------------------------------
def parse_compand_filter(compand_str):
    """
    Extract the 'points=' portion from the compand string and return a list of (x, y) floats.
    """
    # Example: "points=-110/-110|-100/-105|...:gain=4"
    # We'll find the substring that starts with "points=" and goes until the next ':' or end of string
    points_pattern = r'points=([^:]+)'  # capture everything after 'points=' until next ':'
    match = re.search(points_pattern, compand_str)
    if not match:
        return []

    points_str = match.group(1)
    # points_str looks like: "-110/-110|-100/-105|-90/-95|..."

    # Now split by '|'
    pairs = points_str.split('|')
    xy = []
    for pair in pairs:
        if '/' in pair:
            x_str, y_str = pair.split('/')
            try:
                x_val = float(x_str)
                y_val = float(y_str)
                xy.append((x_val, y_val))
            except ValueError:
                pass
    return xy


# --------------------------------------------------------------------
# 3) Rebuild the compand string from a list of (x, y).
#    We preserve the 'attacks=0:decays=0.3:soft-knee=6' etc. from the original,
#    but change the "points=..." section.
# --------------------------------------------------------------------
def rebuild_compand_filter(xy_points, original_str):
    """
    Rebuild the compand filter string, replacing only the "points=" section
    with updated x/y pairs from xy_points.
    """
    # We'll keep everything up to 'points=' the same,
    # and everything after the original 'points=...some...:???' also the same.
    # But we'll rebuild the actual points portion from xy_points.

    # 1) Extract prefix (everything before 'points=')
    prefix_pattern = r'(.*points=)'
    prefix_match = re.search(prefix_pattern, original_str)
    if prefix_match:
        prefix = prefix_match.group(1)
    else:
        prefix = ""

    # 2) Extract suffix (everything after the points list)
    #    That is, find the substring starting from the first ':'
    #    AFTER the "points=..."
    #    A quick trick is to find 'points=' plus the actual pairs, then see what's left.
    points_pattern = r'points=([^:]+)(.*)'
    points_match = re.search(points_pattern, original_str)
    if points_match:
        # The second group starts with the next ':' (like ":gain=4")
        suffix = points_match.group(2)
    else:
        suffix = ""

    # 3) Build new points string
    #    Format each pair as "x/y", then join with '|'
    new_points_str = "|".join(f"{x:.2f}/{y:.2f}" for x, y in xy_points)

    # 4) Combine everything
    new_compand = f"{prefix}{new_points_str}{suffix}"
    return new_compand


# --------------------------------------------------------------------
# 4) A small helper to find the index of the closest point to a given (mouse) x,y
# --------------------------------------------------------------------
def find_closest_point(xy_points, x_click, y_click):
    """
    Return the index of the point in xy_points that is closest to (x_click, y_click).
    """
    dists = [(x - x_click) ** 2 + (y - y_click) ** 2 for x, y in xy_points]
    min_idx = np.argmin(dists)
    return min_idx


# --------------------------------------------------------------------
# 5) GUI / Plot / Interaction
# --------------------------------------------------------------------
class CompandGUI(tk.Tk):
    def __init__(self, compand_str):
        super().__init__()
        self.title("Compand Filter Editor")

        # Parse initial points
        self.xy_points = parse_compand_filter(compand_str)
        self.original_compand = compand_str

        # For dragging
        self.dragging_point_idx = None

        # Create figure and axis
        self.fig, self.ax = plt.subplots(figsize=(6, 4), dpi=100)
        self.ax.set_title("Even-Out-Sound Compand Filter")
        self.ax.set_xlabel("dB in")
        self.ax.set_ylabel("dB out")

        # Plot a simple y=x line as "linear" reference in the background
        # (Feel free to replace with your own reference if needed)
        x_vals = np.linspace(min(x for x, _ in self.xy_points) - 10,
                             max(x for x, _ in self.xy_points) + 10, 200)
        self.ax.plot(x_vals, x_vals, 'k--', alpha=0.3, label="Reference (y=x)")

        # Plot the compand curve
        self.line, = self.ax.plot(
            [p[0] for p in self.xy_points],
            [p[1] for p in self.xy_points],
            'bo-', label="EOS"
        )

        self.ax.legend(loc="best")
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=1)

        # Bind matplotlib events
        self.cid_press = self.canvas.mpl_connect('button_press_event', self.on_click)
        self.cid_release = self.canvas.mpl_connect('button_release_event', self.on_release)
        self.cid_motion = self.canvas.mpl_connect('motion_notify_event', self.on_motion)

        # Text box to display the updated compand filter
        self.text_var = tk.StringVar()
        self.text_var.set(self.original_compand)

        # Use a ttk.Entry or a scrolled Text for display (here we use just a read-only text)
        self.frame_bottom = ttk.Frame(self)
        self.frame_bottom.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(self.frame_bottom, text="Updated Compand Filter:").pack(anchor=tk.W, pady=2)

        self.text_box = tk.Text(self.frame_bottom, height=3, wrap=tk.NONE)
        self.text_box.pack(fill=tk.X, padx=5)
        # Insert initial value
        self.text_box.insert("1.0", self.original_compand)
        # Make it read-only by disabling 'insert' and 'delete'
        self.text_box.configure(state="disabled")

    def on_click(self, event):
        """When mouse is clicked, check if it's close to a point. If so, set up dragging."""
        if event.inaxes != self.ax:
            return
        x_click = event.xdata
        y_click = event.ydata
        self.dragging_point_idx = find_closest_point(self.xy_points, x_click, y_click)

    def on_release(self, event):
        """When mouse is released, finalize dragging."""
        if event.inaxes != self.ax:
            return
        self.dragging_point_idx = None

    def on_motion(self, event):
        """When mouse moves and we are dragging a point, update that point and redraw."""
        if self.dragging_point_idx is None:
            return
        if event.inaxes != self.ax:
            return

        x_new = event.xdata
        y_new = event.ydata
        if x_new is None or y_new is None:
            return

        # Update the point's coordinates
        self.xy_points[self.dragging_point_idx] = (x_new, y_new)

        # Redraw the line
        self.line.set_xdata([p[0] for p in self.xy_points])
        self.line.set_ydata([p[1] for p in self.xy_points])

        self.ax.relim()
        self.ax.autoscale_view()
        self.canvas.draw()

        # Rebuild and update compand string
        new_compand_str = rebuild_compand_filter(self.xy_points, self.original_compand)
        self.update_compand_textbox(new_compand_str)

    def update_compand_textbox(self, new_str):
        """Updates the text box with the new compand string."""
        self.text_box.configure(state="normal")
        self.text_box.delete("1.0", tk.END)
        self.text_box.insert("1.0", new_str)
        self.text_box.configure(state="disabled")


# --------------------------------------------------------------------
# 6) Main
# --------------------------------------------------------------------
if __name__ == "__main__":
    app = CompandGUI(compand_filter_original)
    app.mainloop()
