pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import ".."
import "."

Rectangle {
    id: root

    required property string taskId
    required property string title
    required property string status
    required property real progress
    required property string stage
    required property string speed
    required property string eta
    required property string error
    required property string outputPath
    required property string url
    required property string mode

    implicitHeight: 84
    height: implicitHeight

    radius: Theme.cardRadius
    color: Theme.card
    border.width: 1
    border.color: root.status === "downloading" ? Theme.accent : Theme.border

    function statusColor(s) {
        if (s === "downloading") return Theme.accent;
        if (s === "completed") return Theme.success;
        if (s === "error") return Theme.error;
        if (s === "canceled") return Theme.warning;
        return Theme.textMuted;
    }

    function statusBg(s) {
        if (s === "downloading") return Theme.accentSoft;
        if (s === "completed") return Theme.successSoft;
        if (s === "error") return Theme.errorSoft;
        if (s === "canceled") return Theme.warningSoft;
        return Theme.surfaceElevated;
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 14

        // Mode Icon Pill
        Rectangle {
            Layout.preferredWidth: 38
            Layout.preferredHeight: 38
            radius: 19
            color: Theme.cardAlt
            border.width: 1
            border.color: Theme.border

            Image {
                anchors.centerIn: parent
                width: 16
                height: 16
                source: {
                    if (root.mode === "audio") return Qt.resolvedUrl("../../../assets/branding/v2icons/music-accent-32.png")
                    return Qt.resolvedUrl("../../../assets/branding/v2icons/play-accent-32.png")
                }
                fillMode: Image.PreserveAspectFit
                mipmap: true
            }
        }

        // Center Task Details & Progress
        ColumnLayout {
            Layout.fillWidth: true
            Layout.preferredWidth: 0
            spacing: 6

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 0
                spacing: 8

                Label {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 0
                    text: root.title || root.url || "Media stream"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.bodySize
                    font.bold: true
                    color: Theme.text
                    elide: Text.ElideRight
                    maximumLineCount: 1
                }

                // Status Badge Pill
                Rectangle {
                    Layout.preferredHeight: 20
                    Layout.preferredWidth: statusText.implicitWidth + 14
                    radius: 10
                    color: root.statusBg(root.status)

                    Label {
                        id: statusText
                        anchors.centerIn: parent
                        text: root.status.toUpperCase()
                        font.family: Theme.fontFamily
                        font.pixelSize: 9
                        font.bold: true
                        color: root.statusColor(root.status)
                    }
                }

                // Action Buttons
                VSecondaryButton {
                    visible: root.status === "downloading" || root.status === "queued"
                    text: "Cancel"
                    Layout.preferredHeight: 26
                    onClicked: QueueController.cancelTask(root.taskId)
                }

                VSecondaryButton {
                    visible: root.status === "completed" && root.outputPath !== ""
                    text: "Open Folder"
                    Layout.preferredHeight: 26
                    onClicked: QueueController.openFolder(root.outputPath)
                }

                VSecondaryButton {
                    visible: root.status === "error" || root.status === "canceled"
                    text: "Retry"
                    Layout.preferredHeight: 26
                    onClicked: QueueController.retryTask(root.taskId)
                }
            }

            // Progress Bar (Spans full available content width)
            Rectangle {
                id: progressTrack
                Layout.fillWidth: true
                Layout.preferredHeight: 4
                radius: 2
                color: Theme.surfaceElevated
                visible: root.status === "downloading" || root.status === "completed"

                Rectangle {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    anchors.right: (root.status === "completed" || root.progress >= 100.0 || (root.progress >= 0.999 && root.progress <= 1.0)) ? parent.right : undefined
                    width: anchors.right ? undefined : (parent.width * Math.min(1.0, Math.max(0.0, root.progress <= 1.0 ? root.progress : (root.progress / 100.0))))
                    radius: 2
                    color: root.status === "completed" ? Theme.success : Theme.accent
                }
            }

            // Sub-metrics (Speed, ETA, Stage)
            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                Label {
                    text: root.status === "completed" ? "Completed" : (root.stage || (root.status === "downloading" ? "Downloading payload..." : ""))
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.microSize
                    color: Theme.textDim
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }

                Label {
                    visible: root.status === "downloading" && root.speed !== ""
                    text: root.speed
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.microSize
                    font.bold: true
                    color: Theme.textMuted
                }

                Label {
                    visible: root.status === "downloading" && root.eta !== ""
                    text: "ETA " + root.eta
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.microSize
                    color: Theme.textDim
                }

                Label {
                    visible: root.status === "downloading" || root.status === "completed"
                    text: root.status === "completed" ? "100%" : (Math.round(root.progress <= 1.0 && root.progress > 0.0 ? (root.progress * 100.0) : root.progress) + "%")
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.microSize
                    font.bold: true
                    color: root.status === "completed" ? Theme.success : Theme.accent
                }
            }
        }
    }
}
