 
load(
    "@rules_cc//cc:defs.bzl",
    "objc_library",
)

objc_library(
    name = "ogg",
    enable_modules = True,
    module_name = "ogg",
    srcs = glob([
        "Sources/*.c",
        "Sources/*.h",
    ]),
    hdrs = glob([
        "include/ogg/*.h",
    ]),
    includes = [
        "include",
    ],
    copts = [
    ],
    visibility = [
        "//visibility:public",
    ],
)
