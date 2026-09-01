pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import ".."
import "../components"

ScrollView {
    id: settingsScroll
    clip: true
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

    ColumnLayout {
        width: Math.max(100, settingsScroll.availableWidth - 16)
        spacing: 16

        // Page Header with Polished Save action
        GridLayout {
            Layout.fillWidth: true
            columns: settingsScroll.availableWidth > 640 ? 2 : 1
            columnSpacing: 14
            rowSpacing: 10

            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 0
                spacing: 3

                Label {
                    text: "Settings & Preferences"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.displayTitleSize
                    font.bold: true
                    color: Theme.text
                }

                Label {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 0
                    text: "Configure extraction engine, authentication sessions, format pipelines, and network."
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.bodySize
                    color: Theme.textDim
                    wrapMode: Text.WordWrap
                }
            }

            VPrimaryButton {
                text: "Save Preferences"
                Layout.preferredHeight: 38
                Layout.preferredWidth: 170
                Layout.alignment: settingsScroll.availableWidth > 640 ? (Qt.AlignRight | Qt.AlignVCenter) : Qt.AlignLeft
                onClicked: {
                    Settings.saveSettings()
                }
            }
        }

        // Section 1: Engine Runtime & Maintenance
        VCard {
            Layout.fillWidth: true
            headerTitle: "Engine Runtime & Maintenance"
            headerSubtitle: "Manage bundled yt-dlp binary, updates, and challenge solvers"
            headerIcon: Qt.resolvedUrl("../../../assets/branding/v2icons/gear-accent-32.png")

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 12

                Flow {
                    Layout.fillWidth: true
                    spacing: 8

                    VPrimaryButton {
                        text: Operational.updaterBusy ? "Working..." : "Check & Install Update"
                        enabled: !Operational.updaterBusy
                        Layout.preferredHeight: 34
                        onClicked: Operational.installUpdater()
                    }

                    VSecondaryButton {
                        text: "Roll Back"
                        enabled: !Operational.updaterBusy
                        Layout.preferredHeight: 34
                        onClicked: Operational.rollbackUpdater()
                    }

                    VSecondaryButton {
                        text: "Use Bundled"
                        enabled: !Operational.updaterBusy
                        Layout.preferredHeight: 34
                        onClicked: Operational.useBundledUpdater()
                    }

                    VSecondaryButton {
                        text: "Open Output Folder"
                        Layout.preferredHeight: 34
                        onClicked: Operational.openOutputFolder()
                    }
                }

                // Strictly bounded Runtime Status capsule with text wrapping
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 0
                    implicitHeight: Math.max(38, statusRow.implicitHeight + 14)
                    radius: Theme.controlRadius
                    color: Theme.cardAlt
                    border.width: 1
                    border.color: Theme.border

                    RowLayout {
                        id: statusRow
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: 12
                        anchors.rightMargin: 12
                        spacing: 8

                        Label {
                            text: "Runtime Status:"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.textDim
                        }

                        Label {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 0
                            text: Operational.updaterStatusText
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            color: Theme.text
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                ColumnLayout {
                    Layout.preferredWidth: 240
                    Layout.maximumWidth: 240
                    Layout.fillWidth: false
                    spacing: 4

                    Label {
                        text: "UPDATE CHANNEL"
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.microSize
                        font.bold: true
                        color: Theme.textDim
                    }

                    VComboBox {
                        id: channelCombo
                        Layout.fillWidth: true
                        model: ["Stable", "Nightly", "Master", "Pre-release"]
                        currentIndex: model.indexOf(Settings.ytdlpChannel) !== -1 ? model.indexOf(Settings.ytdlpChannel) : 0
                        onActivated: (idx) => Settings.ytdlpChannel = model[idx]
                    }
                }

                VCheckBox {
                    Layout.fillWidth: true
                    text: "Check update channel at startup (once per 24h)"
                    checked: Settings.ytdlpCheckOnStartup
                    onToggled: Settings.ytdlpCheckOnStartup = checked
                }

                VCheckBox {
                    Layout.fillWidth: true
                    text: "Allow yt-dlp to fetch official challenge-solver components when required"
                    checked: Settings.allowRemoteComponents
                    onToggled: Settings.allowRemoteComponents = checked
                }
            }
        }

        // Section 2: Authentication & Cookies
        VCard {
            Layout.fillWidth: true
            headerTitle: "Authentication & Cookies"
            headerSubtitle: "Browser session extraction for restricted media"
            headerIcon: Qt.resolvedUrl("../../../assets/branding/v2icons/lock-accent-32.png")

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 12

                GridLayout {
                    Layout.fillWidth: true
                    columns: settingsScroll.availableWidth > 680 ? 2 : 1
                    columnSpacing: 14
                    rowSpacing: 8

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        spacing: 4

                        Label {
                            text: "BROWSER COOKIE SOURCE"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.textDim
                        }

                        VComboBox {
                            Layout.fillWidth: true
                            model: ["Disabled", "Auto-detect", "Chrome", "Firefox", "Edge", "Brave", "Opera", "Chromium", "Vivaldi", "Safari", "Custom File"]
                            currentIndex: model.indexOf(Settings.cookieMode) !== -1 ? model.indexOf(Settings.cookieMode) : 0
                            onActivated: (idx) => Settings.cookieMode = model[idx]
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        spacing: 4

                        Label {
                            text: "COOKIE PROFILE"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.textDim
                        }

                        VTextField {
                            Layout.fillWidth: true
                            text: Settings.cookieProfile
                            placeholderText: "Default or profile name"
                            onEditingFinished: Settings.cookieProfile = text
                        }
                    }
                }

                ColumnLayout {
                    visible: Settings.cookieMode === "Custom File"
                    Layout.fillWidth: true
                    spacing: 4

                    Label {
                        text: "COOKIE FILE PATH"
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.microSize
                        font.bold: true
                        color: Theme.textDim
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        VTextField {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 0
                            text: Settings.cookieFile
                            onEditingFinished: Settings.cookieFile = text
                        }

                        VSecondaryButton {
                            text: "Browse"
                            Layout.preferredHeight: Theme.controlHeight
                            onClicked: cookieFileDialog.open()
                        }
                    }
                }

                // Row 1 of buttons: Verification Window + Clear Session
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    VSecondaryButton {
                        text: "Open Verification Window"
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        Layout.preferredHeight: Theme.controlHeight
                        onClicked: Operational.openVerificationWindow()
                    }

                    VSecondaryButton {
                        text: "Clear Session"
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        Layout.preferredHeight: Theme.controlHeight
                        onClicked: Operational.clearBrowserSession()
                    }
                }

                // Row 2 of buttons: Retry After Verification (Full Width Action)
                VPrimaryButton {
                    text: "Retry After Verification"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Theme.controlHeight
                    onClicked: Operational.retryAfterVerification()
                }
            }
        }

        // Section 3: Subtitles & Captions
        VCard {
            Layout.fillWidth: true
            headerTitle: "Subtitles & Captions"
            headerSubtitle: "Subtitle language filters and embedding rules"
            headerIcon: Qt.resolvedUrl("../../../assets/branding/v2icons/subtitles-accent-32.png")

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 10

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4

                    Label {
                        text: "LANGUAGE REGEX PATTERN"
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.microSize
                        font.bold: true
                        color: Theme.textDim
                    }

                    VTextField {
                        Layout.fillWidth: true
                        text: Settings.subLangs
                        placeholderText: "en.*, ja, zh-Hans"
                        onEditingFinished: Settings.subLangs = text
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    VCheckBox {
                        Layout.fillWidth: true
                        text: "Embed subtitles into the video (remuxes to MKV)"
                        checked: Settings.embedSubs
                        onToggled: Settings.embedSubs = checked
                    }

                    VCheckBox {
                        Layout.fillWidth: true
                        text: "Include automatically generated captions"
                        checked: Settings.autoCaptions
                        onToggled: Settings.autoCaptions = checked
                    }

                    VCheckBox {
                        Layout.fillWidth: true
                        text: "Download matching subtitles by default"
                        checked: Settings.downloadSubs
                        onToggled: Settings.downloadSubs = checked
                    }
                }
            }
        }

        // Section 4: Audio, Metadata & SponsorBlock
        VCard {
            Layout.fillWidth: true
            headerTitle: "Media Filters & Metadata"
            headerSubtitle: "Cover art, ID3 tags, and SponsorBlock rules"
            headerIcon: Qt.resolvedUrl("../../../assets/branding/v2icons/music-accent-32.png")

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 10

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    VCheckBox {
                        Layout.fillWidth: true
                        text: "Embed video thumbnail as album cover art"
                        checked: Settings.embedThumbnail
                        onToggled: Settings.embedThumbnail = checked
                    }

                    VCheckBox {
                        Layout.fillWidth: true
                        text: "Embed title, artist, and available metadata tags"
                        checked: Settings.embedMetadata
                        onToggled: Settings.embedMetadata = checked
                    }

                    VCheckBox {
                        Layout.fillWidth: true
                        text: "Remove selected sponsor and advertisement segments"
                        checked: Settings.sponsorblock
                        onToggled: Settings.sponsorblock = checked
                    }
                }

                ColumnLayout {
                    visible: Settings.sponsorblock
                    Layout.fillWidth: true
                    spacing: 4

                    Label {
                        text: "SPONSORBLOCK CATEGORIES"
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.microSize
                        font.bold: true
                        color: Theme.textDim
                    }

                    VTextField {
                        Layout.fillWidth: true
                        text: Settings.sponsorblockCategories
                        placeholderText: "sponsor,selfpromo,interaction,intro,outro"
                        onEditingFinished: Settings.sponsorblockCategories = text
                    }
                }
            }
        }

        // Section 5: Network & File Output
        VCard {
            Layout.fillWidth: true
            headerTitle: "Network & File Output"
            headerSubtitle: "Proxy settings, bandwidth throttling, and filename constraints"
            headerIcon: Qt.resolvedUrl("../../../assets/branding/v2icons/gear-accent-32.png")

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 12

                GridLayout {
                    Layout.fillWidth: true
                    columns: settingsScroll.availableWidth > 680 ? 2 : 1
                    columnSpacing: 14
                    rowSpacing: 8

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        spacing: 4

                        Label {
                            text: "PROXY SERVER URL"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.textDim
                        }

                        VTextField {
                            Layout.fillWidth: true
                            text: Settings.proxy
                            placeholderText: "http://127.0.0.1:8080 or socks5://..."
                            onEditingFinished: Settings.proxy = text
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        spacing: 4

                        Label {
                            text: "RATE LIMIT"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.textDim
                        }

                        VTextField {
                            Layout.fillWidth: true
                            text: Settings.rateLimit
                            placeholderText: "2M or 500K"
                            onEditingFinished: Settings.rateLimit = text
                        }
                    }
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: settingsScroll.availableWidth > 680 ? 2 : 1
                    columnSpacing: 14
                    rowSpacing: 8

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        spacing: 4

                        Label {
                            text: "OUTPUT FILENAME TEMPLATE"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.textDim
                        }

                        VTextField {
                            Layout.fillWidth: true
                            text: Settings.outputTemplate
                            placeholderText: "%(title)s [%(id)s].%(ext)s"
                            onEditingFinished: Settings.outputTemplate = text
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        spacing: 4

                        Label {
                            text: "CLIENT IMPERSONATION"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.textDim
                        }

                        VComboBox {
                            Layout.fillWidth: true
                            model: ["Automatic", "Chrome", "Safari", "Edge", "Firefox", "iOS", "Android"]
                            currentIndex: model.indexOf(Settings.impersonation) !== -1 ? model.indexOf(Settings.impersonation) : 0
                            onActivated: (idx) => Settings.impersonation = model[idx]
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4

                    Label {
                        text: "FORMAT SORTING (-S)"
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.microSize
                        font.bold: true
                        color: Theme.textDim
                    }

                    VTextField {
                        Layout.fillWidth: true
                        text: Settings.formatSort
                        placeholderText: "res,ext:mp4:m4a (optional format sort override)"
                        onEditingFinished: Settings.formatSort = text
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    VCheckBox {
                        Layout.fillWidth: true
                        text: "Force IPv4 connections"
                        checked: Settings.forceIpv4
                        onToggled: Settings.forceIpv4 = checked
                    }

                    VCheckBox {
                        Layout.fillWidth: true
                        text: "Restrict safe ASCII filenames"
                        checked: Settings.restrictFilenames
                        onToggled: Settings.restrictFilenames = checked
                    }

                    VCheckBox {
                        Layout.fillWidth: true
                        text: "Use download archive to skip duplicates"
                        checked: Settings.useArchive
                        onToggled: Settings.useArchive = checked
                    }
                }
            }
        }

        // Section 6: Passive Media Observer
        VCard {
            Layout.fillWidth: true
            headerTitle: "Passive Media Observer"
            headerSubtitle: "Background browser media stream detection & uBOL coexistence"
            headerIcon: Qt.resolvedUrl("../../../assets/branding/v2icons/terminal-accent-32.png")

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 12

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        implicitHeight: Math.max(38, obsRow.implicitHeight + 14)
                        radius: Theme.controlRadius
                        color: Theme.cardAlt
                        border.width: 1
                        border.color: Theme.border

                        RowLayout {
                            id: obsRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12
                            spacing: 8

                            Label {
                                text: "Observer Status:"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.microSize
                                font.bold: true
                                color: Theme.textDim
                            }

                            Label {
                                Layout.fillWidth: true
                                Layout.preferredWidth: 0
                                text: Operational.observerStatusText !== "" ? Operational.observerStatusText : "Passive sensor operational."
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.microSize
                                color: Theme.text
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    VSecondaryButton {
                        text: "Check Update"
                        Layout.preferredHeight: 38
                        onClicked: Operational.checkObserverUpdate()
                    }

                    VPrimaryButton {
                        text: "Apply Update"
                        Layout.preferredHeight: 38
                        onClicked: Operational.applyObserverUpdate()
                    }
                }
            }
        }

        // Section 7: Advanced / Explicit Custom yt-dlp Command (Restored from 3.0)
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: advancedCol.implicitHeight + 28
            radius: Theme.cardRadius
            color: Theme.card
            border.width: 1
            border.color: Settings.useCustomCommand ? Theme.warning : Theme.border

            ColumnLayout {
                id: advancedCol
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    Image {
                        source: Qt.resolvedUrl("../../../assets/branding/v2icons/terminal-accent-32.png")
                        Layout.preferredWidth: 16
                        Layout.preferredHeight: 16
                        fillMode: Image.PreserveAspectFit
                        mipmap: true
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        spacing: 2

                        Label {
                            text: "Advanced: Explicit Custom yt-dlp Command"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.sectionTitleSize
                            font.bold: true
                            color: Settings.useCustomCommand ? Theme.warning : Theme.text
                        }

                        Label {
                            text: "Direct CLI argument injection with safety gate"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            color: Theme.textDim
                        }
                    }
                }

                VCheckBox {
                    Layout.fillWidth: true
                    text: "I understand: use this custom command for the next queued download"
                    checked: Settings.useCustomCommand
                    onToggled: Settings.useCustomCommand = checked
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: safetyCol.implicitHeight + 14
                    radius: Theme.controlRadius
                    color: Theme.warningSoft
                    border.width: 1
                    border.color: Theme.warning
                    opacity: Settings.useCustomCommand ? 1.0 : 0.6

                    ColumnLayout {
                        id: safetyCol
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 2

                        Label {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 0
                            text: "SAFETY GATE  /  Text below is inert until the checkbox is explicitly enabled. Custom mode overrides most normal format options."
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.warning
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                VTextField {
                    Layout.fillWidth: true
                    enabled: Settings.useCustomCommand
                    text: Settings.customCommand
                    placeholderText: "--write-auto-subs --concurrent-fragments 4"
                    onEditingFinished: Settings.customCommand = text
                }
            }
        }

        // Section 8: About & System Information (Without build number in visible UI)
        VCard {
            Layout.fillWidth: true
            headerTitle: "About VRKA"
            headerSubtitle: "Engine metadata, copyright, and third-party notices"
            headerIcon: Qt.resolvedUrl("../../../assets/branding/vrka-wolf-256.png")

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 16

                    Image {
                        source: Qt.resolvedUrl("../../../assets/branding/vrka-wolf-256.png")
                        Layout.preferredWidth: 48
                        Layout.preferredHeight: 48
                        fillMode: Image.PreserveAspectFit
                        mipmap: true
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        spacing: 2

                        Label {
                            text: "VRKA Media Engine — Version " + APP_DISPLAY_VERSION
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.bodySize
                            font.bold: true
                            color: Theme.text
                        }

                        Label {
                            text: "High-performance generic media downloader and passive web capture suite."
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.smallSize
                            color: Theme.textMuted
                        }

                        Label {
                            text: "All third-party components are licensed under verified permissive licenses."
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            color: Theme.textDim
                        }
                    }

                    VSecondaryButton {
                        text: "View Notices"
                        Layout.preferredHeight: 34
                        onClicked: Operational.openOutputFolder()
                    }
                }
            }
        }
    }

    FileDialog {
        id: cookieFileDialog
        title: "Select Cookie File"
        nameFilters: ["Text files (*.txt)", "All files (*)"]
        onAccepted: {
            var path = selectedFile.toString();
            if (path.startsWith("file:///")) {
                path = path.substring(8);
            }
            Settings.cookieFile = path;
        }
    }
}
