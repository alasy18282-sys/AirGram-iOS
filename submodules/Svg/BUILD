
load(
    "@rules_cc//cc:defs.bzl",
    "objc_library",
)

objc_library(
    name = "Svg",
    enable_modules = True,
    module_name = "Svg",
    srcs = glob([
        "Sources/**/*.m",
        "Sources/**/*.c",
        "Sources/**/*.h",
    ]),
    hdrs = glob([
        "PublicHeaders/**/*.h",
    ]),
    includes = [
        "PublicHeaders",
    ],
    sdk_frameworks = [
        "Foundation",
    ],
    visibility = [
        "//visibility:public",
    ],
)
