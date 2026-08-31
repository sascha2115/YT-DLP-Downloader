import time
from AppKit import NSApplication, NSImage, NSProgressIndicator, NSMakeRect, NSObject, NSView

app = NSApplication.sharedApplication()
dockTile = app.dockTile()

# Create a view for the dock tile
view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, dockTile.size().width, dockTile.size().height))

# Create progress indicator
progressIndicator = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(0, 10, dockTile.size().width, 20))
progressIndicator.setIndeterminate_(False)
progressIndicator.setMaxValue_(100)
progressIndicator.setDoubleValue_(0)

view.addSubview_(progressIndicator)
dockTile.setContentView_(view)

for i in range(11):
    progressIndicator.setDoubleValue_(i * 10)
    dockTile.display()
    print("Progress:", i * 10)
    time.sleep(0.5)

dockTile.setContentView_(None)
dockTile.display()
