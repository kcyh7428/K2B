#include <CoreFoundation/CoreFoundation.h>
#include <NetFS/NetFS.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc != 2 && argc != 3) return 2;

    CFURLRef url = CFURLCreateWithBytes(
        NULL, (const UInt8 *)argv[1], strlen(argv[1]), kCFStringEncodingUTF8, NULL);
    CFURLRef directory = argc == 3
        ? CFURLCreateFromFileSystemRepresentation(
            NULL, (const UInt8 *)argv[2], strlen(argv[2]), true)
        : NULL;
    if (!url || (argc == 3 && !directory)) return 2;
    CFMutableDictionaryRef session = CFDictionaryCreateMutable(
        NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFMutableDictionaryRef mount = CFDictionaryCreateMutable(
        NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFDictionarySetValue(session, kNAUIOptionKey, kNAUIOptionNoUI);
    if (directory) {
        CFDictionarySetValue(session, kNetFSForceNewSessionKey, kCFBooleanTrue);
        CFDictionarySetValue(mount, kNetFSMountAtMountDirKey, kCFBooleanTrue);
    }
    CFDictionarySetValue(mount, kNetFSSoftMountKey, kCFBooleanFalse);

    CFArrayRef points = NULL;
    int result = NetFSMountURLSync(url, directory, NULL, NULL, session, mount, &points);
    if (result != 0) fprintf(stderr, "macOS SMB connection error %d\n", result);
    if (points) CFRelease(points);
    if (directory) CFRelease(directory);
    CFRelease(mount);
    CFRelease(session);
    CFRelease(url);
    return result != 0;
}
