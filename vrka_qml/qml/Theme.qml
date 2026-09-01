pragma Singleton
pragma ComponentBehavior: Bound
import QtQuick

QtObject {
    id: root

    property string mode: "dark"
    readonly property bool isLight: mode === "light"

    // Exact Authoritative VRKA 3.0 Semantic Colors
    readonly property color bg:              isLight ? "#FFFFFF" : "#000000"
    readonly property color sidebar:         isLight ? "#F7F7F8" : "#050505"
    readonly property color card:            isLight ? "#F7F7F8" : "#090909"
    readonly property color cardAlt:         isLight ? "#EFEFF1" : "#111111"
    readonly property color surfaceElevated: isLight ? "#E8E8EB" : "#161616"
    readonly property color surfaceHover:    isLight ? "#DEDEE3" : "#202020"
    readonly property color border:          isLight ? "#DEDEE3" : "#242424"
    readonly property color borderStrong:    isLight ? "#C8C8CF" : "#363636"

    // VRKA Signature Accent
    readonly property color accent:          "#8140DC"
    readonly property color accentHover:     "#9255E5"
    readonly property color accentPressed:   "#6E31C3"
    readonly property color accentSoft:      isLight ? "#F1E8FC" : "#180D24"
    readonly property color accentSoftHover: isLight ? "#E8D9FA" : "#241236"
    readonly property color focusRing:       "#B98AF2"

    // Text hierarchy
    readonly property color text:         isLight ? "#141216" : "#FAF9FC"
    readonly property color textMuted:    isLight ? "#4E4B54" : "#C8C4CF"
    readonly property color textDim:      isLight ? "#6B6871" : "#98939F"
    readonly property color textDisabled: isLight ? "#85818B" : "#817C88"
    readonly property color textOnAccent: "#FFFFFF"

    // Semantics
    readonly property color success:      "#2BCB77"
    readonly property color successSoft:  isLight ? "#EBF9F1" : "#0B1F14"
    readonly property color warning:      "#E7A93D"
    readonly property color warningSoft:  isLight ? "#FDF6EB" : "#241804"
    readonly property color error:        "#EF5A67"
    readonly property color errorSoft:    isLight ? "#FDEEF0" : "#270A0E"

    // Frosted Material Levels (Material 0 - 4)
    readonly property color material0: bg
    readonly property color material1: sidebar
    readonly property color material2: card
    readonly property color material3: cardAlt
    readonly property color material4: surfaceElevated

    // Spacing & geometry system
    readonly property int sidebarWidth:         240
    readonly property int navButtonHeight:      46
    readonly property int controlHeight:        42
    readonly property int controlRadius:        8
    readonly property int cardRadius:           12
    readonly property int primaryButtonHeight:  48
    readonly property int pagePadX:             32
    readonly property int pagePadY:             24
    readonly property int cardPadX:             20
    readonly property int hairline:             1

    // Space Mono Typography Hierarchy
    readonly property string fontFamily:       "Space Mono"
    readonly property int    logoSize:       72
    readonly property int    brandTitleSize:   26
    readonly property int    displayTitleSize: 28
    readonly property int    pageTitleSize:    24
    readonly property int    sectionTitleSize: 16
    readonly property int    bodySize:         14
    readonly property int    smallSize:        12
    readonly property int    microSize:        11
}
