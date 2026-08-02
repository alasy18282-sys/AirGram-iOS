#import <UIKit/UIKit.h>
#import <dlfcn.h>

static void loadSideloadFixerIfPresent(void) {
    NSString *dylibPath = [[NSBundle mainBundle] pathForResource:@"sideloadFixerLol" ofType:@"dylib"];
    if (dylibPath.length == 0) {
        return;
    }
    
    void *handle = dlopen(dylibPath.UTF8String, RTLD_NOW | RTLD_GLOBAL);
    if (handle == NULL) {
        const char *error = dlerror();
        NSLog(@"Failed to load sideloadFixerLol.dylib: %s", error != NULL ? error : "unknown");
    }
}

int main(int argc, char *argv[]) {
    @autoreleasepool {
        loadSideloadFixerIfPresent();
        return UIApplicationMain(argc, argv, @"Application", @"AppDelegate");
    }
}
